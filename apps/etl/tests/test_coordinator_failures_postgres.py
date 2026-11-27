"""Tiny transaction checks; local execution requires separate owner authorization.

The disconnect injection closes only this test's own psycopg connection, never a
shared session. Post-commit InterruptedError is SIMULATED acknowledgment loss;
it is not evidence of a real network failure, server crash or crash durability.
"""

import csv
import hashlib
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import psycopg
import pytest
from decouple import config
from django.db import DatabaseError, InterfaceError, connections
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from etl import ingestion_completion as completion
from etl import pipeline_runs as pipeline
from etl.exceptions import AttemptConflictError, StagingIntegrityError
from etl.models import (
    IngestionCompletionProof,
    PipelineRun,
    StagingBatch,
    StagingIngestion,
    StagingRecord,
    TransformationAttempt,
    TransformationRecordResult,
)
from etl.sources import CANONICAL_COLUMNS, CsvSource, ExcelSource
from etl.staging_reader import load_source_batch
from etl.tests.test_ingestion_completion import binding as binding
from etl.tests.test_ingestion_completion import complete
from etl.tests.test_pipeline_runs import args as args
from etl.tests.test_source_snapshots import store as store
from etl.tests.test_source_snapshots import workbook
from etl.tests.test_staging_constraints import staging_postgres as disposable_postgres  # noqa: F401
from etl.tests.test_staging_persistence import application_postgres as application_postgres
from etl.tests.test_staging_persistence import batch as batch
from etl.tests.test_staging_persistence import ingestion as ingestion
from etl.tests.test_staging_reader import _raw_snapshot
from etl.transforms.date_contract import SourceDateProfile


@pytest.fixture(scope="module")
def staging_postgres(request):
    """Check the application server before the shared fixture provisions its UUID database."""
    host = config("POSTGRES_HOST", default="127.0.0.1")
    if (
        host not in {"localhost", "127.0.0.1", "::1"}
        or config("POSTGRES_PORT", default="5432") != "5432"
        or any(os.environ.get(key) for key in ("PGHOSTADDR", "PGSERVICE", "PGOPTIONS"))
    ):
        pytest.fail("Expected the dedicated local application test server.", pytrace=False)
    try:
        with psycopg.connect(
            dbname="postgres",
            host=host,
            port=5432,
            user=config("POSTGRES_USER", default="dataxai"),
            password=config("POSTGRES_PASSWORD", default="dataxai"),
            connect_timeout=3,
        ) as admin:
            permission = admin.execute(
                "SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname = current_user"
            ).fetchone()
    except psycopg.Error:
        permission = None
    if permission != (True,):
        pytest.fail("Application fixture requires local login and CREATE DATABASE.", pytrace=False)
    return request.getfixturevalue("disposable_postgres")


def database():
    db = connections["default"]
    assert db.vendor == "postgresql"
    assert re.fullmatch(r"test_dataxai_staging_[0-9a-f]{32}", db.settings_dict["NAME"])
    return db


def failure_events(caplog):
    return [
        record.etl
        for record in caplog.records
        if isinstance(record.msg, str) and record.msg.startswith("etl.coordinator_failure ")
    ]


def execution_counts():
    # Include unlinked runs and other execution keys, not just the intended identity.
    return TransformationAttempt.objects.count(), PipelineRun.objects.count()


def assert_exact_execution(ingestion_id, execution_key, expected_outcomes, totals_before):
    """Exact final fixture accounting across all execution keys in this ingestion."""
    batches = list(StagingBatch.objects.filter(ingestion_id=ingestion_id).order_by("batch_number"))
    row_counts = [sum(outcomes) for outcomes in expected_outcomes]
    assert [(b.batch_number, b.row_count) for b in batches] == list(enumerate(row_counts))
    attempts = list(
        TransformationAttempt.objects.filter(batch__ingestion_id=ingestion_id).order_by(
            "batch__batch_number"
        )
    )
    assert [
        (a.batch_id, a.attempt_key, a.status, a.safe_row_count, a.unresolved_row_count)
        for a in attempts
    ] == [
        (b.pk, execution_key, "SUCCEEDED", safe, unresolved)
        for b, (safe, unresolved) in zip(batches, expected_outcomes, strict=True)
    ]
    assert all(
        a.finished_at is not None and a.claim_owner is None and a.claim_expires_at is None
        for a in attempts
    )
    runs = PipelineRun.objects.filter(attempt__batch__ingestion_id=ingestion_id).order_by(
        "attempt__batch__batch_number"
    )
    assert list(runs.values_list("attempt_id", "status", "records_ingested")) == [
        (a.pk, "succeeded", count) for a, count in zip(attempts, row_counts, strict=True)
    ]
    # Global deltas also detect unexpected unlinked runs; the fixture is isolated and serial.
    assert execution_counts() == tuple(count + len(batches) for count in totals_before)
    records = StagingRecord.objects.filter(batch__ingestion_id=ingestion_id)
    rows = TransformationRecordResult.objects.filter(batch__ingestion_id=ingestion_id)
    assert records.count() == rows.count() == sum(row_counts)
    assert rows.filter(result_payload__isnull=True).count() == sum(row_counts)
    assert set(rows.values_list("staged_record_id", flat=True)) == set(
        records.values_list("pk", flat=True)
    )
    for attempt, (safe, unresolved) in zip(attempts, expected_outcomes, strict=True):
        assert rows.filter(attempt_id=attempt.pk, outcome="SAFE").count() == safe
        assert rows.filter(attempt_id=attempt.pk, outcome="UNRESOLVED").count() == unresolved


def disconnect_after_write(db, table):
    """Allow a real uncommitted write, close OUR socket, then encounter a real driver error."""
    state = {"fired": False}

    def execute(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if not state["fired"] and sql.startswith((f'INSERT INTO "{table}"', f'UPDATE "{table}"')):
            assert db.in_atomic_block and not db.get_autocommit()
            state["fired"] = True
            db.connection.close()
            # The already-closed client connection makes this driver operation fail.
            execute("SELECT 1", None, False, context)
            pytest.fail("Closed test connection accepted a query")
        return result

    return state, execute


@pytest.mark.parametrize("point", ["staging", "outcomes"])
def test_application_disconnect_preserves_committed_prefix(args, batch, monkeypatch, caplog, point):
    db = database()
    totals_before = execution_counts()
    prefix = pipeline.stage_and_run_batch(**args, batch=batch)
    prefix_identity = (prefix.pk, prefix.updated_at, prefix.attempt_id)
    second = replace(batch, batch_number=1)
    table = "etl_stagingrecord" if point == "staging" else "etl_transformationrecordresult"
    injected, wrapper = disconnect_after_write(db, table)
    with db.execute_wrapper(wrapper), pytest.raises((DatabaseError, InterfaceError)):
        pipeline.stage_and_run_batch(**args, batch=second)
    assert injected["fired"]
    (event,) = failure_events(caplog)
    assert event["stage"] == ("staging" if point == "staging" else "finalization")
    assert event["category"] == "database"
    # The real driver's SQLSTATE may be absent; do not invent a transient reason.
    assert event["recovery"] in ("operator_review", "reconcile_first")
    db.close()
    db.ensure_connection()
    parent = StagingIngestion.objects.get(pk=args["ingestion_id"])
    assert (parent.status, parent.final_batch_count, parent.final_row_count) == ("OPEN", None, None)
    prefix.refresh_from_db()
    assert (prefix.pk, prefix.updated_at, prefix.attempt_id) == prefix_identity
    assert prefix.status == "succeeded" and prefix.attempt.results.count() == len(batch)
    attempts = TransformationAttempt.objects.filter(batch__ingestion=parent, batch__batch_number=1)
    if point == "staging":
        assert parent.batches.count() == 1 and not attempts.exists()
    else:
        attempt = attempts.get()
        identity = (attempt.pk, attempt.batch_id, attempt.pipeline_run.pk)
        assert parent.batches.count() == 2 and attempt.batch.records.count() == len(batch)
        assert attempt.status == "RUNNING" and attempt.pipeline_run.status == "running"
        assert attempt.finished_at is None and not attempt.results.exists()
        with pytest.raises(AttemptConflictError):
            pipeline.run_staged_batch(**args, batch_number=1)
        # Explicit test-only expiry AFTER reconciliation; production never does this automatically.
        attempts.update(claim_expires_at=timezone.now() - timedelta(seconds=1))
    recovered = pipeline.stage_and_run_batch(**args, batch=second)
    assert recovered.status == "succeeded" and recovered.attempt.results.count() == len(batch)
    assert recovered.attempt.results.filter(result_payload__isnull=True).count() == len(batch)
    if point == "outcomes":
        assert (recovered.attempt_id, recovered.attempt.batch_id, recovered.pk) == identity
        assert recovered.attempt.claim_generation == 2
    assert parent.batches.count() == 2
    assert_exact_execution(
        parent.pk, args["attempt_key"], [(len(batch), 0), (len(batch), 0)], totals_before
    )


def test_application_disconnect_cannot_partially_seal(binding, caplog):
    db = database()
    totals_before = execution_counts()
    proof = completion.bind_file_ingestion(**binding)
    injected, wrapper = disconnect_after_write(db, "etl_ingestioncompletionproof")
    with db.execute_wrapper(wrapper), pytest.raises((DatabaseError, InterfaceError)):
        complete(binding)
    assert injected["fired"]
    (event,) = failure_events(caplog)
    assert event["stage"] == "sealing" and event["category"] == "database"
    db.close()
    db.ensure_connection()
    proof.refresh_from_db()
    assert proof.sealed_at is None and proof.receipt_digest is None
    assert proof.ingestion.status == "OPEN" and proof.ingestion.final_row_count is None
    rows = TransformationRecordResult.objects.filter(batch__ingestion_id=proof.pk)
    identities = set(rows.values_list("pk", "attempt_id", "staged_record_id"))
    assert len(identities) == rows.filter(result_payload__isnull=True).count() == 2
    recovered = complete(binding)
    assert recovered.successful_batches == 2
    assert recovered.execution_key == proof.execution_key == binding["execution_key"]
    assert set(rows.values_list("pk", "attempt_id", "staged_record_id")) == identities
    assert_exact_execution(proof.pk, proof.execution_key, [(1, 0), (0, 1)], totals_before)


@pytest.mark.parametrize("point", ["staging", "finalization", "sealing"])
def test_simulated_post_commit_ack_loss_reconciles_original_identities(
    binding, monkeypatch, caplog, point
):
    db = database()
    totals_before = execution_counts()
    proof = completion.bind_file_ingestion(**binding)
    table = {
        "staging": "etl_stagingbatch",
        "finalization": "etl_pipelinerun",
        "sealing": "etl_ingestioncompletionproof",
    }[point]
    original_commit = db.commit
    injected = {"armed": False, "fired": False}
    lost_ack = InterruptedError("simulated acknowledgment loss AFTER successful commit")

    def observe(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if sql.startswith((f'INSERT INTO "{table}"', f'UPDATE "{table}"')):
            if point != "finalization" or "succeeded" in params:
                injected["armed"] = True
        return result

    def commit_then_interrupt():
        original_commit()
        if injected["armed"] and not injected["fired"]:
            injected["fired"] = True
            raise lost_ack

    with monkeypatch.context() as patch:
        patch.setattr(db, "commit", commit_then_interrupt)
        with db.execute_wrapper(observe), pytest.raises(InterruptedError) as caught:
            complete(binding)
    assert caught.value is lost_ack and injected["fired"]
    (event,) = failure_events(caplog)
    assert event["stage"] == point
    assert event["category"] == "interrupted" and event["recovery"] == "reconcile_first"
    # Read persisted evidence under the ORIGINAL ingestion/execution identities first.
    proof.refresh_from_db()
    batches = proof.ingestion.batches
    assert batches.count() == (2 if point == "sealing" else 1)
    attempts = TransformationAttempt.objects.filter(batch__ingestion_id=proof.pk)
    runs = PipelineRun.objects.filter(attempt__in=attempts)
    rows = TransformationRecordResult.objects.filter(batch__ingestion_id=proof.pk)
    if point == "staging":
        assert not attempts.exists() and not rows.exists()
    else:
        assert not attempts.exclude(status="SUCCEEDED", attempt_key=proof.execution_key).exists()
        assert rows.count() == rows.filter(result_payload__isnull=True).count() == batches.count()
    assert proof.ingestion.status == ("COMPLETE" if point == "sealing" else "OPEN")
    assert (proof.sealed_at is not None) == (point == "sealing")
    attempt_identity_fields = (
        "pk",
        "batch_id",
        "attempt_key",
        "claim_generation",
        "finished_at",
        "updated_at",
    )
    before = (
        set(batches.values_list("pk", "batch_number", "content_hash")),
        set(attempts.values_list(*attempt_identity_fields)),
        set(runs.values_list("pk", "attempt_id", "updated_at")),
        set(rows.values_list("pk", "attempt_id", "staged_record_id")),
    )
    if point == "sealing":
        monkeypatch.setattr(
            completion, "open_snapshot_csv", lambda *a, **k: pytest.fail("Sealed source reopened")
        )
    with CaptureQueriesContext(db) as queries:
        acknowledged = complete(binding)
    assert acknowledged.pk == proof.pk
    assert acknowledged.execution_key == proof.execution_key == binding["execution_key"]
    assert acknowledged.sealed_at is not None and acknowledged.receipt_digest
    assert acknowledged.binding_digest == proof.binding_digest
    assert (
        acknowledged.source_batches,
        acknowledged.staged_batches,
        acknowledged.successful_batches,
        acknowledged.source_rows,
        acknowledged.staged_rows,
        acknowledged.safe_rows,
        acknowledged.unresolved_rows,
    ) == (2, 2, 2, 2, 2, 1, 1)
    parent = StagingIngestion.objects.get(pk=proof.pk)
    assert (
        parent.status,
        parent.final_batch_count,
        parent.final_row_count,
        parent.completed_at,
    ) == (
        "COMPLETE",
        2,
        2,
        acknowledged.sealed_at,
    )
    assert_exact_execution(proof.pk, binding["execution_key"], [(1, 0), (0, 1)], totals_before)
    after = (
        set(batches.values_list("pk", "batch_number", "content_hash")),
        set(attempts.values_list(*attempt_identity_fields)),
        set(runs.values_list("pk", "attempt_id", "updated_at")),
        set(rows.values_list("pk", "attempt_id", "staged_record_id")),
    )
    assert all(prior <= final for prior, final in zip(before, after, strict=True))
    if point == "sealing":
        assert after == before
        assert (acknowledged.receipt_digest, acknowledged.sealed_at) == (
            proof.receipt_digest,
            proof.sealed_at,
        )
        assert not any(
            q["sql"].lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"} for q in queries
        )


def test_broken_sink_preserves_persisted_receipt(binding):
    """Use an actual handler failure through inherited source and completion logs."""
    import logging

    class BrokenSink(logging.Handler):
        def emit(self, record):
            raise RuntimeError("synthetic sink failure")

    logger = logging.getLogger("etl")
    sink = BrokenSink()
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(sink)
    try:
        totals_before = execution_counts()
        completion.bind_file_ingestion(**binding)
        receipt = complete(binding)
        assert receipt.ingestion.status == "COMPLETE"
        acknowledged = complete(binding)
        assert acknowledged.receipt_digest == receipt.receipt_digest
        assert acknowledged.execution_key == binding["execution_key"]
        assert_exact_execution(
            receipt.pk, binding["execution_key"], [(1, 0), (0, 1)], totals_before
        )
    finally:
        logger.removeHandler(sink)
        logger.setLevel(previous_level)
        sink.close()


def _completion_state():
    """All persisted columns, including other keys and unlinked runs in the serial fixture."""
    return {
        model: {row[model._meta.pk.attname]: row for row in model.objects.values()}
        for model in (
            StagingIngestion,
            IngestionCompletionProof,
            StagingBatch,
            StagingRecord,
            TransformationAttempt,
            PipelineRun,
            TransformationRecordResult,
        )
    }


@pytest.mark.parametrize("kind", ["CSV", "XLSX"])
def test_snapshot_cleanup_failure_preserves_unsealed_execution_and_recovers(
    kind, tmp_path, store, monkeypatch, caplog
):
    db = database()
    rows = [
        ["C001", "M", "Mug", -2, "2026-10-20 14:30:00", 1.20, 17, "Pakistan"],
        ["C002", "M", "Mug", -2, None, 1.20, 17, "Pakistan"],
    ]
    if kind == "CSV":
        path = tmp_path / "orders.csv"
        with path.open("w", newline="", encoding="utf-8") as stream:
            csv.writer(stream).writerows([CANONICAL_COLUMNS, *rows])
        scope = completion.CsvScope(path.name, 1)
        adapter, resource_name, opener = CsvSource, "_reader", "open_snapshot_csv"
        source_name = f"csv:{path.name}"
        coverage = {"csv_header": "verified", "exhaustion": "natural"}
    else:
        path = workbook(
            tmp_path, {"Empty": [CANONICAL_COLUMNS], "Orders": [CANONICAL_COLUMNS, *rows]}
        )
        scope = completion.ExcelScope(path.name, 1, ("Orders",))
        adapter, resource_name, opener = ExcelSource, "_workbook", "open_snapshot_excel"
        source_name = f"xlsx:{path.name}:Orders"
        coverage = {
            "selection": "selected",
            "exhaustion": "natural",
            "sheets": [{"name": "Orders", "header": "verified", "rows": 2, "batches": 2}],
        }
    ingestion = StagingIngestion.objects.create(
        source_kind="CSV" if kind == "CSV" else "EXCEL", source_reference=path.name
    )
    key = uuid4()
    original = path.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    proof = completion.bind_file_ingestion(
        ingestion_id=ingestion.pk,
        input_path=path,
        scope=scope,
        execution_key=key,
        profile=SourceDateProfile(),
        transformation_version="proof-v1",
        expected_sha256=digest,
        expected_batch_count=2,
        expected_row_count=2,
    )
    snapshot = store / digest
    assert snapshot.read_bytes() == original
    assert proof.pk == ingestion.pk and proof.execution_key == key and proof.bound_at is not None
    assert (proof.snapshot_sha256, proof.snapshot_size) == (digest, len(original))
    assert (proof.source_kind, proof.source_reference, proof.batch_size) == (
        ingestion.source_kind,
        path.name,
        1,
    )
    assert proof.parser_recipe == completion._recipe(scope)
    assert proof.binding_digest == completion._binding_digest(proof)
    counts = {
        "source_batches": 2,
        "source_rows": 2,
        "staged_batches": 2,
        "staged_rows": 2,
        "successful_batches": 2,
        "safe_rows": 1,
        "unresolved_rows": 1,
    }
    before = _completion_state()
    bound = before[IngestionCompletionProof][proof.pk]
    assert {k: getattr(proof, k) for k in bound} == bound
    parent = before[StagingIngestion][proof.pk]
    assert parent["status"] == "OPEN"
    assert all(parent[k] is None for k in ("completed_at", "final_row_count", "final_batch_count"))
    seal_fields = (*counts, "coverage", "manifest_digest", "sealed_at", "receipt_digest")
    assert all(bound[k] is None for k in seal_fields)
    totals = execution_counts()
    with db.cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        (writer_pid,) = cursor.fetchone()

    def raw_staging():
        # Compare the stored SER-v1 text before Django's field decoding.
        return [_raw_snapshot(batch) for batch in ingestion.batches.order_by("batch_number")]

    def observe_commit():
        observer = database()
        try:
            with observer.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid(), current_database()")
                pid, name = cursor.fetchone()
            assert pid != writer_pid and name == db.settings_dict["NAME"]
            assert_exact_execution(proof.pk, key, [(1, 0), (0, 1)], totals)
            state = _completion_state()
            for model, prior in before.items():
                delta = 0 if model in (StagingIngestion, IngestionCompletionProof) else 2
                assert len(state[model]) == len(prior) + delta
                assert {pk: state[model][pk] for pk in prior} == prior
            return state, raw_staging()
        finally:
            observer.close()

    real_open = getattr(completion, opener)
    traversals = []

    @contextmanager
    def observe_open(identity, **kwargs):
        assert identity == completion.SourceSnapshot(digest, len(original))
        with real_open(identity, **kwargs) as traversal:
            traversals.append(traversal)
            yield traversal

    committed = []
    fault = OSError(f"private-cleanup-marker token=synthetic-only {path}")
    real_exit = adapter.__exit__

    def interrupted_exit(source, *exc_info):
        resource = getattr(source, resource_name)
        real_close = resource.close

        def close():
            try:
                (traversal,) = traversals
                assert traversal._exhausted and not traversal.verified_exhausted
                with ThreadPoolExecutor(max_workers=1) as pool:
                    state = pool.submit(observe_commit).result(timeout=30)
            finally:
                real_close()
            committed.append(state)
            raise fault

        # pandas may close at iterator EOF. Wrap only the adapter's final close.
        with monkeypatch.context() as patch:
            patch.setattr(resource, "close", close)
            return real_exit(source, *exc_info)

    def finish():
        return completion.complete_bound_ingestion(ingestion_id=proof.pk, claim_ttl_seconds=60)

    with monkeypatch.context() as watch:
        watch.setattr(completion, opener, observe_open)
        with monkeypatch.context() as inject:
            inject.setattr(adapter, "__exit__", interrupted_exit)
            with pytest.raises(OSError) as caught:
                finish()
        assert caught.value is fault
        ((at_close, original_staging),) = committed
        (failed,) = traversals
        assert not failed.verified_exhausted and failed.postcheck_seconds is None
        assert _completion_state() == at_close and raw_staging() == original_staging
        assert snapshot.read_bytes() == original

        expected = {
            "operation": "complete_bound_ingestion",
            "stage": "source_cleanup",
            "ingestion_id": str(proof.pk),
            "execution_key": str(key),
            "observation": "exception",
            "code": "etl_error",
            "category": "source_io",
            "reason": "unspecified",
            "message": "An I/O operation failed. Inspect resource availability and permissions.",
            "recovery": "operator_review",
        }
        assert failure_events(caplog) == [expected]
        (event,) = [r for r in caplog.records if hasattr(r, "etl")]
        assert event.name == completion.__name__ and event.levelno == logging.WARNING
        assert event.args == ()
        assert event.exc_info is event.stack_info is None
        assert logging.Formatter("%(message)s").format(event) == (
            "etl.coordinator_failure " + json.dumps(expected, sort_keys=True)
        )

        manifest = completion._Manifest()
        for number, staged in enumerate(ingestion.batches.order_by("batch_number")):
            batch = load_source_batch(ingestion_id=proof.pk, batch_number=number)
            assert (batch.source_name, batch.batch_number) == (source_name, number)
            assert list(batch.data.index) == [number if kind == "CSV" else 0]
            assert batch.data["Invoice"].tolist() == [rows[number][0]]
            record = staged.records.get()
            outcome = record.results.get()
            assert record.row_position == record.record_payload["row_position"] == 0
            assert outcome.batch_id == outcome.attempt.batch_id == staged.pk
            manifest.add(number, source_name, 1, staged.content_hash)
        recovered = finish()
        assert len(traversals) == 2 and traversals[1].verified_exhausted

    assert recovered.pk == proof.pk and recovered.execution_key == key
    assert recovered.sealed_at is not None and recovered.sealed_at >= proof.bound_at
    assert recovered.receipt_digest == completion._receipt_digest(recovered)
    assert_exact_execution(proof.pk, key, [(1, 0), (0, 1)], totals)
    sealed = _completion_state()
    receipt = {
        **bound,
        **counts,
        "coverage": coverage,
        "manifest_digest": manifest.digest,
        "receipt_digest": recovered.receipt_digest,
        "sealed_at": recovered.sealed_at,
    }
    assert {k: getattr(recovered, k) for k in receipt} == receipt
    completed_parent = {
        **parent,
        "status": "COMPLETE",
        "final_row_count": 2,
        "final_batch_count": 2,
        "completed_at": recovered.sealed_at,
        "updated_at": sealed[StagingIngestion][proof.pk]["updated_at"],
    }
    assert sealed == {
        **at_close,
        StagingIngestion: {**at_close[StagingIngestion], proof.pk: completed_parent},
        IngestionCompletionProof: {**at_close[IngestionCompletionProof], proof.pk: receipt},
    }
    assert raw_staging() == original_staging and snapshot.read_bytes() == original

    def forbid_open(*args, **kwargs):
        pytest.fail("Sealed acknowledgment reopened the snapshot")

    with monkeypatch.context() as patch:
        patch.setattr(completion, "open_snapshot_csv", forbid_open)
        patch.setattr(completion, "open_snapshot_excel", forbid_open)
        with CaptureQueriesContext(db) as activity:
            acknowledged = finish()
        # Sealed acknowledgment still checks the stored parser recipe.
        patch.setattr(completion.pandas, "__version__", "incompatible")
        with pytest.raises(StagingIntegrityError, match="Incompatible parser recipe or runtime"):
            finish()
    assert activity.captured_queries
    for query in activity:
        # The verifier locks the receipt with SELECT ... FOR UPDATE.
        sql = re.sub(r"\bFOR\s+(?:NO\s+KEY\s+)?UPDATE\b", "", query["sql"], flags=re.I)
        assert not re.search(r"\b(?:INSERT|UPDATE|DELETE)\b", sql, re.I)
    assert {k: getattr(acknowledged, k) for k in receipt} == receipt
    assert _completion_state() == sealed and raw_staging() == original_staging
