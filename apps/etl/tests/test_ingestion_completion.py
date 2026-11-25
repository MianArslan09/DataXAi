"""Completion services against the project's UUID-named disposable PostgreSQL database."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import IntegrityError, connections, transaction
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.executor import MigrationExecutor
from django.db.models import JSONField, Value
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from etl import ingestion_completion as service
from etl.attempt_claims import claim_attempt
from etl.exceptions import (
    AttemptConflictError,
    MalformedSourceError,
    StagingIngestionError,
    StagingIntegrityError,
)
from etl.models import (
    IngestionCompletionProof,
    PipelineRun,
    StagingBatch,
    StagingIngestion,
    TransformationAttempt,
    TransformationRecordResult,
)
from etl.source_snapshots import SnapshotError
from etl.sources.contract import CANONICAL_COLUMNS
from etl.staging import stage_source_batch
from etl.tests.test_source_snapshots import rewrite_object
from etl.tests.test_source_snapshots import store as store
from etl.tests.test_source_snapshots import workbook
from etl.tests.test_staging_constraints import staging_postgres as staging_postgres
from etl.tests.test_staging_persistence import application_postgres as application_postgres
from etl.tests.test_staging_persistence import batch as batch
from etl.transformation_attempts import fail_attempt, register_attempt
from etl.transforms.date_contract import SourceDateProfile


@pytest.mark.parametrize(
    "authorized,name", [(False, "dataxai_completion_review"), (True, "dataxai")]
)
def test_owner_harness_rejects_unsafe_target_before_database_guard(monkeypatch, authorized, name):
    from scripts import verify_bound_completion as harness

    def unexpected(*args):
        pytest.fail("Unsafe request reached the database guard")

    monkeypatch.setattr(harness, "_database_guard", unexpected)
    with pytest.raises(ValueError):
        harness.measure(
            SimpleNamespace(authorize_benchmark_writes=authorized, expected_db=name), {}
        )


FIXTURES = Path(__file__).parent / "fixtures"
PREVIOUS = ("etl", "0003_pipelinerun_attempt")
TIP = ("etl", "0004_ingestion_completion_proof")


@pytest.fixture
def binding(store, tmp_path):
    path = tmp_path / "orders.csv"
    path.write_text(
        "Invoice,StockCode,Description,Quantity,InvoiceDate,Price,Customer ID,Country\n"
        "C001,M,Mug,-2,2026-10-20 14:30:00,1.20,17,Pakistan\n"
        "C001,M,Mug,-2,,1.20,17,Pakistan\n",
        encoding="utf-8",
    )
    ingestion = StagingIngestion.objects.create(source_kind="CSV", source_reference=path.name)
    return dict(
        ingestion_id=ingestion.pk,
        input_path=path,
        scope=service.CsvScope(path.name, 1),
        execution_key=uuid4(),
        profile=SourceDateProfile(),
        transformation_version="proof-v1",
    )


def test_bind_replay_conflict_and_immutable_columns(binding, batch):
    proof = service.bind_file_ingestion(**binding)
    stage_source_batch(ingestion_id=proof.pk, batch=batch)
    retry = service.bind_file_ingestion(**binding)
    assert retry.pk == proof.pk and retry.bound_at == proof.bound_at
    with pytest.raises(StagingIntegrityError):
        service.bind_file_ingestion(**{**binding, "execution_key": uuid4()})
    for changes in ({"source_kind": "POSTGRES"}, {"execution_key": uuid4()}, {"safe_rows": 0}):
        with pytest.raises(IntegrityError), transaction.atomic():
            IngestionCompletionProof.objects.filter(pk=proof.pk).update(**changes)
    proof.refresh_from_db()
    assert proof.sealed_at is None and proof.execution_key == binding["execution_key"]


@pytest.mark.parametrize("case", ["retroactive", "aborted", "postgres", "name", "hash", "bool"])
def test_reject_invalid_binding(binding, batch, case):
    ingestion = StagingIngestion.objects.get(pk=binding["ingestion_id"])
    if case == "retroactive":
        stage_source_batch(ingestion_id=ingestion.pk, batch=batch)
    elif case == "aborted":
        StagingIngestion.objects.filter(pk=ingestion.pk).update(
            status="ABORTED", aborted_at=timezone.now()
        )
    elif case == "postgres":
        StagingIngestion.objects.filter(pk=ingestion.pk).update(source_kind="POSTGRES")
    elif case == "name":
        binding["scope"] = service.CsvScope("other.csv")
    elif case == "hash":
        binding["expected_sha256"] = "a" * 64
    else:
        binding["expected_row_count"] = True
    with pytest.raises((StagingIntegrityError, SnapshotError)):
        service.bind_file_ingestion(**binding)
    assert not IngestionCompletionProof.objects.filter(pk=ingestion.pk).exists()


@pytest.mark.parametrize("mode", ["empty", "open", "sealed"])
def test_reverse_guard(mode, django_db_blocker):
    # Separate connections/databases per case, regardless of test order. The
    # fixture for ordinary service tests intentionally shares a DB per module.
    from etl.tests.test_staging_constraints import staging_postgres as provision

    with contextmanager(provision.__wrapped__)(django_db_blocker) as alias:
        connection = connections[alias]
        assert connection.settings_dict["NAME"].startswith("test_dataxai_staging_")
        if mode == "empty":
            MigrationExecutor(connection).migrate([PREVIOUS])
            assert (
                IngestionCompletionProof._meta.db_table
                not in connection.introspection.table_names()
            )
            MigrationExecutor(connection).migrate([TIP])
        else:
            ingestion = StagingIngestion.objects.using(alias).create(
                source_kind="CSV", source_reference="test.csv"
            )
            now = timezone.now()
            proofs = IngestionCompletionProof.objects.using(alias)
            proofs.create(
                ingestion=ingestion,
                bound_at=now,
                source_kind="CSV",
                source_reference="test.csv",
                snapshot_sha256="c" * 64,
                snapshot_size=1,
                parser_recipe={},
                batch_size=1,
                execution_key=uuid4(),
                transformation_version="test",
                configuration={},
                configuration_hash="a" * 64,
                binding_digest="b" * 64,
            )
            if mode == "sealed":
                proofs.update(
                    source_batches=0,
                    source_rows=0,
                    staged_batches=0,
                    staged_rows=0,
                    successful_batches=0,
                    safe_rows=0,
                    unresolved_rows=0,
                    manifest_digest="d" * 64,
                    coverage={},
                    sealed_at=now,
                    receipt_digest="e" * 64,
                )
            with pytest.raises(IrreversibleError):
                MigrationExecutor(connection).migrate([PREVIOUS])
            assert TIP in MigrationExecutor(connection).loader.applied_migrations
            assert proofs.get().sealed_at == (now if mode == "sealed" else None)


def complete(binding):
    return service.complete_bound_ingestion(
        ingestion_id=binding["ingestion_id"], claim_ttl_seconds=60
    )


def await_seal(binding, monkeypatch):
    proof = service.bind_file_ingestion(**binding)
    with monkeypatch.context() as patch:
        patch.setattr(service, "_seal", lambda *args: args[-1])
        observed = complete(binding)
    assert proof.ingestion.status == "OPEN" and observed is not None
    return proof, observed


def test_csv_complete_and_read_only_ack(binding, monkeypatch, batch):
    original = service.bind_file_ingestion(**binding)
    proof = complete(binding)
    assert (proof.pk, proof.bound_at) == (original.pk, original.bound_at)
    proof.refresh_from_db()
    assert (proof.source_batches, proof.successful_batches, proof.staged_batches) == (2, 2, 2)
    assert (proof.source_rows, proof.safe_rows, proof.unresolved_rows) == (2, 1, 1)
    proof.ingestion.refresh_from_db()
    assert (
        proof.ingestion.status,
        proof.ingestion.completed_at,
        proof.ingestion.final_row_count,
        proof.ingestion.final_batch_count,
    ) == ("COMPLETE", proof.sealed_at, 2, 2)
    monkeypatch.setattr(
        service, "open_snapshot_csv", lambda *a, **k: pytest.fail("source reopened")
    )
    with CaptureQueriesContext(connections["default"]) as activity:
        assert complete(binding).receipt_digest == proof.receipt_digest
    assert not any(
        q["sql"].lstrip().split()[0].upper() in ("UPDATE", "INSERT", "DELETE") for q in activity
    )
    with pytest.raises(StagingIngestionError):
        stage_source_batch(ingestion_id=proof.pk, batch=replace(batch, batch_number=2))


@pytest.mark.parametrize("selection", [None, ("Data", "Empty"), ("Empty",), ("Missing",)])
def test_excel_coverage(binding, tmp_path, selection):
    row = ["C100", "M", "Mug", -2, "2026-11-10 14:30:00", 2.5, 17, "Pakistan"]
    path = workbook(tmp_path, {"Empty": [CANONICAL_COLUMNS], "Data": [CANONICAL_COLUMNS, row, row]})
    StagingIngestion.objects.filter(pk=binding["ingestion_id"]).update(
        source_kind="EXCEL", source_reference=path.name
    )
    binding.update(scope=service.ExcelScope(path.name, 1, selection), input_path=path)
    proof = service.bind_file_ingestion(**binding)
    if selection == ("Missing",):
        with pytest.raises(MalformedSourceError):
            complete(binding)
        proof.refresh_from_db()
        assert proof.sealed_at is None
        return
    proof = complete(binding)
    sheets = selection or ("Empty", "Data")
    assert proof.coverage["selection"] == ("all" if selection is None else "selected")
    assert proof.coverage["sheets"] == [
        dict(
            name=name,
            header="verified",
            rows=2 if name == "Data" else 0,
            batches=2 if name == "Data" else 0,
        )
        for name in sheets
    ]
    assert proof.staged_rows == proof.safe_rows == (2 if "Data" in sheets else 0)
    if "Data" in sheets:
        assert proof.ingestion.batches.values("content_hash").distinct().count() == 1


def test_header_only_csv(binding):
    binding["input_path"].write_text(",".join(CANONICAL_COLUMNS) + "\n", encoding="utf-8")
    service.bind_file_ingestion(**binding)
    receipt = complete(binding)
    assert (
        receipt.source_batches == 1
        and receipt.staged_rows == receipt.safe_rows == receipt.unresolved_rows == 0
    )


@pytest.mark.parametrize("when", ["before", "prefix", "last"])
def test_recovery_from_snapshot(binding, monkeypatch, when):
    proof = service.bind_file_ingestion(**binding)
    binding["input_path"].write_bytes(b"replaced")
    real = service._stage_with_digest
    with monkeypatch.context() as patch:

        def interrupt(**kwargs):
            if kwargs["batch"].batch_number == (0 if when == "before" else 1):
                raise InterruptedError
            return real(**kwargs)

        if when == "last":

            def stop(*args):
                raise InterruptedError

            patch.setattr(service, "_seal", stop)
        else:
            patch.setattr(service, "_stage_with_digest", interrupt)
        with pytest.raises(InterruptedError):
            complete(binding)
    proof.refresh_from_db()
    batches = proof.ingestion.batches
    assert batches.count() == {"before": 0, "prefix": 1, "last": 2}[when]
    assert proof.sealed_at is None and proof.ingestion.status == "OPEN"
    runs = PipelineRun.objects.filter(attempt__batch__ingestion_id=proof.pk)
    results = TransformationRecordResult.objects.filter(batch__ingestion_id=proof.pk)
    previous = (
        set(batches.values_list("pk", flat=True)),
        set(runs.values_list("pk", "updated_at")),
        set(results.values_list("pk", "staged_record_id")),
    )
    assert complete(binding).source_rows == 2
    assert previous[0] <= set(batches.values_list("pk", flat=True))
    assert previous[1] <= set(runs.values_list("pk", "updated_at"))
    assert previous[2] <= set(results.values_list("pk", "staged_record_id"))


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "extra",
        "sequence",
        "position",
        "source_hash",
        "attempt_key",
        "attempt_missing",
        "failed",
        "run",
        "accounting",
        "json_null",
        "configuration",
        "header",
    ],
)
def test_corruption_at_seal_boundary(binding, monkeypatch, batch, fault):
    proof, observed = await_seal(binding, monkeypatch)
    staged = proof.ingestion.batches.get(batch_number=0)
    attempt = staged.attempts.get()
    if fault == "extra":
        stage_source_batch(ingestion_id=proof.pk, batch=replace(batch, batch_number=2))
    elif fault == "missing":
        last = proof.ingestion.batches.get(batch_number=1)
        last.result_rows.all().delete()
        PipelineRun.objects.filter(attempt__batch=last).delete()
        last.attempts.all().delete()
        last.records.all().delete()
        last.delete()
    elif fault == "sequence":
        StagingBatch.objects.filter(pk=staged.pk).update(batch_number=8)
    elif fault == "position":
        staged.records.update(row_position=7)
    elif fault == "source_hash":
        StagingBatch.objects.filter(pk=staged.pk).update(content_hash="0" * 64)
    elif fault == "header":
        StagingBatch.objects.filter(pk=staged.pk).update(header={**staged.header, "row_count": 77})
    elif fault == "attempt_key":
        staged.attempts.update(attempt_key=uuid4())
    elif fault == "attempt_missing":
        attempt.results.all().delete()
        attempt.pipeline_run.delete()
        attempt.delete()
    elif fault == "failed":
        staged.attempts.update(status="FAILED", safe_row_count=None, unresolved_row_count=None)
    elif fault == "run":
        PipelineRun.objects.filter(attempt=attempt).update(status="failed")
    elif fault == "accounting":
        attempt.results.update(outcome="UNRESOLVED")
    elif fault == "json_null":
        attempt.results.update(result_payload=Value(None, output_field=JSONField()))
    else:
        staged.attempts.update(configuration={})
    with pytest.raises(StagingIntegrityError):
        service._seal(proof.pk, proof.binding_digest, observed)
    proof.refresh_from_db()
    assert proof.sealed_at is None and proof.ingestion.status == "OPEN"


@pytest.mark.parametrize(
    "case", ["missing", "corrupt", "runtime", "expected", "unbound", "aborted"]
)
def test_invalid_recovery(binding, store, case, monkeypatch):
    if case == "unbound":
        with pytest.raises(StagingIngestionError):
            complete(binding)
        return
    if case == "expected":
        binding["expected_row_count"] = 3
    proof = service.bind_file_ingestion(**binding)
    if case == "missing":
        (store / proof.snapshot_sha256).rename(store / "removed")
    elif case == "corrupt":
        rewrite_object(
            store / proof.snapshot_sha256,
            binding["input_path"].read_bytes().replace(b"Mug", b"Jug"),
        )
    elif case == "runtime":
        monkeypatch.setattr(service.pandas, "__version__", "incompatible")
    elif case == "aborted":
        StagingIngestion.objects.filter(pk=proof.pk).update(
            status="ABORTED", aborted_at=timezone.now()
        )
    with pytest.raises((SnapshotError, StagingIntegrityError)):
        complete(binding)
    proof.refresh_from_db()
    assert proof.sealed_at is None


def test_finalization_rollback(binding, monkeypatch):
    proof, observed = await_seal(binding, monkeypatch)

    def fail(*args, **kwargs):
        raise RuntimeError("injected")

    monkeypatch.setattr(StagingIngestion, "save", fail)
    with pytest.raises(RuntimeError, match="injected"):
        service._seal(proof.pk, proof.binding_digest, observed)
    proof.refresh_from_db()
    assert (
        proof.receipt_digest is None
        and proof.ingestion.status == "OPEN"
        and proof.ingestion.batches.count() == 2
    )


@pytest.mark.parametrize("mode", ["failed", "active-then-expired"])
def test_authoritative_attempt_lifecycle(binding, mode):
    proof = service.bind_file_ingestion(**binding)
    with service.open_snapshot_csv(
        service.SourceSnapshot(proof.snapshot_sha256, proof.snapshot_size),
        filename="orders.csv",
        batch_size=1,
    ) as traversal:
        batch = next(iter(traversal))
        stage_source_batch(ingestion_id=proof.pk, batch=batch)
    attempt = register_attempt(
        ingestion_id=proof.pk,
        batch_number=0,
        attempt_key=proof.execution_key,
        profile=binding["profile"],
        transformation_version=proof.transformation_version,
    )
    if mode == "failed":
        fail_attempt(
            attempt_id=attempt.pk,
            failure_code="TYPE_CONVERSION_ERROR",
            failure_stage="INTERPRETATION",
        )
        with pytest.raises(StagingIntegrityError):
            complete(binding)
    else:
        claim_attempt(attempt_id=attempt.pk, ttl_seconds=120)
        with pytest.raises(AttemptConflictError):
            complete(binding)
        TransformationAttempt.objects.filter(pk=attempt.pk).update(claim_expires_at=timezone.now())
        assert complete(binding).successful_batches == 2
        attempt.refresh_from_db()
        assert attempt.claim_generation == 2


def _race(left, right):
    start = Barrier(2, timeout=10)

    def worker(action):
        db = connections["default"]
        try:
            with db.cursor() as cursor:
                cursor.execute("SET lock_timeout='10s'")
                cursor.execute("SELECT pg_backend_pid()")
                pid = cursor.fetchone()[0]
            try:
                value = action(start)
            except (StagingIntegrityError, StagingIngestionError) as error:
                value = type(error)
            return pid, value
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(worker, action) for action in (left, right)]
        (pid1, a), (pid2, b) = [f.result(timeout=30) for f in results]
    assert pid1 != pid2
    return a, b


@pytest.mark.parametrize("conflict", [False, True])
def test_competing_binders(binding, conflict, monkeypatch):
    start = Barrier(2, timeout=10)
    original = service._locked_ingestion

    def synchronize(identity):
        start.wait()
        return original(identity)

    monkeypatch.setattr(service, "_locked_ingestion", synchronize)

    def bind(options, barrier):
        barrier.wait()
        return service.bind_file_ingestion(**options).binding_digest

    other = {**binding, "execution_key": uuid4()} if conflict else binding
    results = _race(lambda b: bind(binding, b), lambda b: bind(other, b))
    proof = IngestionCompletionProof.objects.get(pk=binding["ingestion_id"])
    assert results.count(proof.binding_digest) == (1 if conflict else 2)
    assert results.count(StagingIntegrityError) == int(conflict)


@pytest.mark.parametrize("conflict", [False, True])
def test_competing_finalizers(binding, monkeypatch, conflict):
    proof, observed = await_seal(binding, monkeypatch)

    def seal(barrier, evidence):
        barrier.wait()
        return service._seal(proof.pk, proof.binding_digest, evidence).sealed_at

    other = replace(observed, digest="0" * 64) if conflict else observed
    a, b = _race(lambda barrier: seal(barrier, observed), lambda barrier: seal(barrier, other))
    proof.refresh_from_db()
    assert proof.sealed_at == proof.ingestion.completed_at == a
    assert b == (StagingIntegrityError if conflict else a)
    assert proof.ingestion.batches.count() == 2


@pytest.mark.parametrize("append_first", [False, True])
def test_append_and_seal_share_parent_lock(binding, monkeypatch, batch, append_first):
    proof, observed = await_seal(binding, monkeypatch)

    def append():
        return stage_source_batch(
            ingestion_id=proof.pk, batch=replace(batch, batch_number=2)
        ).batch_number

    def seal():
        return service._seal(proof.pk, proof.binding_digest, observed).staged_batches

    first, second = (append, seal) if append_first else (seal, append)

    def holder(barrier):
        with transaction.atomic():
            StagingIngestion.objects.select_for_update().get(pk=proof.pk)
            barrier.wait()
            return first()

    def contender(barrier):
        barrier.wait()
        return second()

    assert _race(holder, contender) == (
        2,
        StagingIntegrityError if append_first else StagingIngestionError,
    )
    proof.refresh_from_db()
    assert proof.ingestion.batches.count() == (3 if append_first else 2)
    assert (proof.sealed_at is None) == append_first


def test_sealed_immutability_and_staged_retry(binding):
    service.bind_file_ingestion(**binding)
    proof = complete(binding)
    with pytest.raises(IntegrityError), transaction.atomic():
        IngestionCompletionProof.objects.filter(pk=proof.pk).update(receipt_digest="1" * 64)
    with service.open_snapshot_csv(
        service.SourceSnapshot(proof.snapshot_sha256, proof.snapshot_size),
        filename="orders.csv",
        batch_size=1,
    ) as traversal:
        original = proof.ingestion.batches.get(batch_number=0)
        assert (
            stage_source_batch(ingestion_id=proof.pk, batch=next(iter(traversal))).pk == original.pk
        )
    proof.refresh_from_db()
    assert proof.receipt_digest == service._receipt_digest(proof)


def test_declared_counts_and_receipt_db_constraints(binding):
    proof = service.bind_file_ingestion(**binding, expected_row_count=2, expected_batch_count=2)
    updates = dict(
        source_batches=2,
        source_rows=2,
        staged_batches=2,
        staged_rows=2,
        successful_batches=2,
        safe_rows=1,
        unresolved_rows=1,
        manifest_digest="d" * 64,
        coverage={},
        sealed_at=timezone.now(),
        receipt_digest="f" * 64,
    )
    for wrong in (
        {"source_rows": 1},
        {"staged_rows": 3},
        {"successful_batches": 1},
        {"safe_rows": 0},
        {"source_batches": 3, "staged_batches": 3, "successful_batches": 3},
        {"coverage": Value(None, output_field=JSONField())},
    ):
        with pytest.raises(IntegrityError), transaction.atomic():
            IngestionCompletionProof.objects.filter(pk=proof.pk).update(**{**updates, **wrong})
    proof.refresh_from_db()
    assert proof.sealed_at is None
