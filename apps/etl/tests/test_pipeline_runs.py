"""Volume 4.5.2 coordinator against the application alias of a disposable PostgreSQL DB."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.contrib import admin
from django.db import IntegrityError, connections, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models.deletion import ProtectedError
from django.utils import timezone

from accounts.models import User
from etl import attempt_claims
from etl import pipeline_runs as service
from etl import transformation_attempts
from etl.attempt_claims import claim_attempt
from etl.exceptions import AttemptConflictError, AttemptIntegrityError, AttemptRegistrationError
from etl.models import PipelineRun, TransformationAttempt
from etl.staging import stage_source_batch
from etl.tests.test_staging_constraints import staging_postgres as staging_postgres
from etl.tests.test_staging_persistence import application_postgres as application_postgres
from etl.tests.test_staging_persistence import batch as batch
from etl.tests.test_staging_persistence import ingestion as ingestion
from etl.tests.test_staging_reader import _raw_snapshot
from etl.transformation_attempts import fail_attempt, register_attempt
from etl.transforms.date_contract import SourceDateProfile


@pytest.fixture
def args(ingestion):
    return dict(
        ingestion_id=ingestion.pk,
        attempt_key=uuid4(),
        profile=SourceDateProfile(),
        transformation_version="pipeline-v1",
        claim_ttl_seconds=60,
    )


def register(args, batch):
    stage_source_batch(ingestion_id=args["ingestion_id"], batch=batch)
    identity = {k: v for k, v in args.items() if k != "claim_ttl_seconds"}
    return register_attempt(**identity, batch_number=batch.batch_number)


def expire(attempt):
    TransformationAttempt.objects.filter(pk=attempt.pk).update(
        claim_expires_at=timezone.now() - timedelta(seconds=5)
    )


@pytest.mark.parametrize("rows,unresolved", [(3, 0), (3, 1), (3, 3), (0, 0)])
def test_accounting_replay_and_separate_attempts(args, ingestion, batch, rows, unresolved):
    frame = batch.data.iloc[:rows].copy()
    frame.iloc[:unresolved, frame.columns.get_loc("InvoiceDate")] = None
    source = replace(batch, data=frame)
    writes = []

    def capture(execute, sql, params, many, context):
        if sql.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            writes.append(sql.split()[1:3])
        return execute(sql, params, many, context)

    with connections["default"].execute_wrapper(capture):
        run = service.stage_and_run_batch(**args, batch=source)
    assert all(any('"etl_' in part for part in target) for target in writes)
    assert run.status == "succeeded"
    assert (run.started_at, run.finished_at) == (run.attempt.started_at, run.attempt.finished_at)
    assert (run.records_ingested, run.records_healed, run.records_quarantined) == (rows, 0, 0)
    assert run.cdqi_scores == run.fault_counts == {}
    assert (run.attempt.safe_row_count, run.attempt.unresolved_row_count) == (
        rows - unresolved,
        unresolved,
    )
    assert list(
        run.attempt.results.order_by("staged_record__row_position").values_list(
            "staged_record__row_position", "outcome"
        )
    ) == [(i, "UNRESOLVED" if i < unresolved else "SAFE") for i in range(rows)]
    assert run.attempt.results.filter(result_payload__isnull=True).count() == rows
    writes.clear()
    with connections["default"].execute_wrapper(capture):
        replay = service.run_staged_batch(**args, batch_number=0)
    assert writes == []
    assert (replay.pk, replay.updated_at) == (run.pk, run.updated_at)
    second = service.stage_and_run_batch(**{**args, "attempt_key": uuid4()}, batch=source)
    assert second.pk != run.pk and second.attempt.batch_id == run.attempt.batch_id
    assert second.records_ingested == rows
    ingestion.refresh_from_db()
    assert (ingestion.status, ingestion.final_batch_count, ingestion.final_row_count) == (
        "OPEN",
        None,
        None,
    )


def test_controlled_conversion_failure_and_repair(args, batch):
    batch.data.iloc[0, batch.data.columns.get_loc("Quantity")] = "sensitive-invalid-value"
    run = service.stage_and_run_batch(**args, batch=batch)
    assert run.status == "failed" and run.attempt.failure_code == "TYPE_CONVERSION_ERROR"
    assert run.records_ingested == 3 and not run.attempt.results.exists()
    assert run.notes == "" and run.attempt.failure_diagnostics == {"input_rows": 3}
    pk, finished = run.pk, run.finished_at
    PipelineRun.objects.filter(pk=pk).update(status="pending", started_at=None, finished_at=None)
    repaired = service.run_staged_batch(**args, batch_number=0)
    assert (repaired.pk, repaired.status, repaired.finished_at) == (pk, "failed", finished)


def test_failure_before_start_and_historical_run(args, batch):
    legacy = PipelineRun.objects.create(notes="historical")
    attempt = register(args, batch)
    failed = fail_attempt(attempt_id=attempt.pk, failure_code="STOP", failure_stage="ADMISSION")
    run = service.run_staged_batch(**args, batch_number=0)
    assert run.pk != legacy.pk and legacy.attempt_id is None
    assert (run.status, run.started_at, run.finished_at) == ("failed", None, failed.finished_at)
    with pytest.raises(ProtectedError):
        attempt.delete()
    with pytest.raises(IntegrityError), transaction.atomic():
        PipelineRun.objects.create(attempt=attempt)


def test_admin_protects_managed_run_only(args, batch):
    request = SimpleNamespace(user=SimpleNamespace(has_perm=lambda perm: True))
    model_admin = admin.site._registry[PipelineRun]
    run = service.stage_and_run_batch(**args, batch=batch)
    assert "attempt" in model_admin.get_readonly_fields(request)
    assert not model_admin.has_change_permission(request, run)
    assert not model_admin.has_delete_permission(request, run)
    legacy = PipelineRun.objects.create()
    assert model_admin.has_change_permission(request, legacy)
    assert model_admin.has_delete_permission(request, legacy)


@pytest.mark.parametrize(
    "change",
    [
        {"ingestion_id": "bad"},
        {"ingestion_id": uuid4()},
        {"batch_number": True},
        {"batch_number": 1},
        {"claim_ttl_seconds": False},
    ],
)
def test_invalid_or_missing_identity(args, batch, change):
    staged = stage_source_batch(ingestion_id=args["ingestion_id"], batch=batch)
    with pytest.raises(AttemptRegistrationError):
        service.run_staged_batch(**{**args, "batch_number": 0, **change})
    assert not staged.attempts.exists()


def test_conflicting_registration_preserves_terminal_run(args, batch):
    run = service.stage_and_run_batch(**args, batch=batch)
    for change in ({"profile": SourceDateProfile(name="other")}, {"transformation_version": "v2"}):
        with pytest.raises(AttemptConflictError):
            service.run_staged_batch(**{**args, **change}, batch_number=0)
    assert PipelineRun.objects.get(pk=run.pk).updated_at == run.updated_at


def test_ambient_transactions_rejected_before_staging(args, batch, ingestion):
    with transaction.atomic(), pytest.raises(AttemptRegistrationError, match="autocommit"):
        service.stage_and_run_batch(**args, batch=batch)
    connection = connections["default"]
    connection.set_autocommit(False)
    try:
        with pytest.raises(AttemptRegistrationError, match="autocommit"):
            service.run_staged_batch(**args, batch_number=0)
    finally:
        connection.rollback()
        connection.set_autocommit(True)
    assert not ingestion.batches.exists()


def test_counter_overflow_rolls_back_admission(args, batch):
    staged = stage_source_batch(ingestion_id=args["ingestion_id"], batch=batch)
    type(staged).objects.filter(pk=staged.pk).update(row_count=2**31)
    with pytest.raises(AttemptIntegrityError, match="counter range"):
        service.run_staged_batch(**args, batch_number=0)
    assert not staged.attempts.exists()


def test_incomplete_staged_positions_prevent_admission(args, batch):
    staged = stage_source_batch(ingestion_id=args["ingestion_id"], batch=batch)
    staged.records.filter(row_position=1).update(row_position=4)
    with pytest.raises(AttemptIntegrityError, match="positions"):
        service.run_staged_batch(**args, batch_number=0)
    assert not staged.attempts.exists()


def test_interrupted_before_first_claim_leaves_one_pending_run(args, batch, monkeypatch):
    def interrupt(**kwargs):
        attempt = TransformationAttempt.objects.get(pk=kwargs["attempt_id"])
        assert attempt.pipeline_run.status == "pending"
        raise ConnectionError("injected claim interruption")

    with monkeypatch.context() as patch:
        patch.setattr(service, "claim_attempt", interrupt)
        with pytest.raises(ConnectionError):
            service.stage_and_run_batch(**args, batch=batch)
    attempt = TransformationAttempt.objects.get(attempt_key=args["attempt_key"])
    pending = attempt.pipeline_run
    assert attempt.status == "REGISTERED" and pending.started_at is None
    run = service.run_staged_batch(**args, batch_number=0)
    assert run.pk == pending.pk and run.status == "succeeded"


@pytest.mark.parametrize("missing", [False, True])
def test_reconcile_running_then_explicit_recovery(args, batch, missing):
    attempt = register(args, batch)
    if not missing:
        PipelineRun.objects.create(attempt=attempt, records_ingested=3)
    claim_attempt(attempt_id=attempt.pk, ttl_seconds=60)
    with pytest.raises(AttemptConflictError, match="active claim"):
        service.run_staged_batch(**args, batch_number=0)
    intermediate = PipelineRun.objects.get(attempt=attempt)
    assert (
        intermediate.status == "running"
        and intermediate.started_at == intermediate.attempt.started_at
    )
    expire(attempt)
    run = service.run_staged_batch(**args, batch_number=0)
    assert run.pk == intermediate.pk and run.started_at == intermediate.started_at
    assert run.status == "succeeded" and run.attempt.claim_generation == 2


@pytest.mark.parametrize("lag", ["absent", "pending", "running"])
def test_reconcile_verified_success_and_reject_contradictions(args, batch, lag):
    run = service.stage_and_run_batch(**args, batch=batch)
    saved = (run.started_at, run.finished_at)
    selected = PipelineRun.objects.filter(pk=run.pk)
    if lag == "absent":
        selected.delete()
    else:
        selected.update(
            status=lag, started_at=saved[0] if lag == "running" else None, finished_at=None
        )
    repaired = service.run_staged_batch(**args, batch_number=0)
    assert (repaired.status, repaired.started_at, repaired.finished_at) == ("succeeded", *saved)
    PipelineRun.objects.filter(pk=repaired.pk).update(status="failed")
    with pytest.raises(AttemptIntegrityError):
        service.run_staged_batch(**args, batch_number=0)
    assert PipelineRun.objects.get(pk=repaired.pk).status == "failed"


def test_corrupt_ledger_never_repairs_terminal_projection(args, batch):
    run = service.stage_and_run_batch(**args, batch=batch)
    run.attempt.results.first().delete()
    run.delete()
    with pytest.raises(AttemptIntegrityError):
        service.run_staged_batch(**args, batch_number=0)
    assert not PipelineRun.objects.filter(attempt_id=run.attempt_id).exists()


def test_projection_failure_rolls_back_success_and_ledger(args, batch, monkeypatch):
    project = service._project

    def interrupt(attempt):
        if attempt.status == "SUCCEEDED":
            raise RuntimeError("injected finalization interruption")
        return project(attempt)

    monkeypatch.setattr(service, "_project", interrupt)
    with pytest.raises(RuntimeError, match="interruption"):
        service.stage_and_run_batch(**args, batch=batch)
    attempt = TransformationAttempt.objects.get(attempt_key=args["attempt_key"])
    assert attempt.pipeline_run.status == "running" and attempt.status == "RUNNING"
    assert attempt.finished_at is None and not attempt.results.exists()
    assert attempt.batch.records.count() == 3


def test_expiry_during_pandas_and_takeover_fences_old_worker(args, batch, monkeypatch):
    apply = service.DateNormalizer.apply

    def takeover(self, mapped):
        assert connections["default"].get_autocommit()
        attempt = TransformationAttempt.objects.get(attempt_key=args["attempt_key"])
        expire(attempt)
        claim_attempt(attempt_id=attempt.pk, ttl_seconds=60)
        return apply(self, mapped)

    monkeypatch.setattr(service.DateNormalizer, "apply", takeover)
    with pytest.raises(AttemptConflictError):
        service.stage_and_run_batch(**args, batch=batch)
    attempt = TransformationAttempt.objects.get(attempt_key=args["attempt_key"])
    assert attempt.claim_generation == 2 and attempt.status == "RUNNING"
    assert attempt.pipeline_run.status == "running" and not attempt.results.exists()


def test_expiry_during_ledger_verification_rolls_back_finalization(args, batch, monkeypatch):
    verify = transformation_attempts._verify_ledger
    database_now = attempt_claims._database_now

    def run_out_of_time(attempt, ledger):
        verify(attempt, ledger)
        assert attempt.results.count() == 3
        monkeypatch.setattr(
            attempt_claims, "_database_now", lambda: database_now() + timedelta(hours=1)
        )

    monkeypatch.setattr(transformation_attempts, "_verify_ledger", run_out_of_time)
    with pytest.raises(AttemptConflictError, match="expired"):
        service.stage_and_run_batch(**args, batch=batch)
    attempt = TransformationAttempt.objects.get(attempt_key=args["attempt_key"])
    assert attempt.status == "RUNNING" and attempt.pipeline_run.status == "running"
    assert not attempt.results.exists() and attempt.batch.records.count() == 3


def test_concurrent_first_admission(args, batch):
    stage_source_batch(ingestion_id=args["ingestion_id"], batch=batch)
    barrier = Barrier(2, timeout=10)

    def worker():
        try:
            barrier.wait()
            try:
                return service.run_staged_batch(**args, batch_number=0).pk
            except AttemptConflictError as error:
                assert "active claim" in str(error)
                return None
        finally:
            connections["default"].close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        ids = [future.result(timeout=30) for future in futures]
    attempt = TransformationAttempt.objects.get(attempt_key=args["attempt_key"])
    assert set(ids) - {None} == {attempt.pipeline_run.pk}
    assert attempt.claim_generation == 1 and attempt.results.count() == 3


def test_managed_initiator_replay_and_user_deletion(args, batch, ingestion):
    user = User.objects.create(username=uuid4().hex)
    ingestion.initiated_by = user
    ingestion.save(update_fields=["initiated_by"])
    run = service.stage_and_run_batch(**args, batch=batch)
    assert run.triggered_by_id == user.pk
    other = User.objects.create(username=uuid4().hex)
    for wrong in (None, other.pk):
        PipelineRun.objects.filter(pk=run.pk).update(triggered_by_id=wrong)
        with pytest.raises(AttemptIntegrityError, match="initiator"):
            service.run_staged_batch(**args, batch_number=0)
        run.refresh_from_db()
        assert run.triggered_by_id == wrong
    PipelineRun.objects.filter(pk=run.pk).update(triggered_by_id=user.pk)
    user.delete()
    ingestion.refresh_from_db()
    replay = service.run_staged_batch(**args, batch_number=0)
    assert replay.pk == run.pk and replay.updated_at == run.updated_at
    assert replay.triggered_by_id is None and ingestion.initiated_by_id is None


@pytest.mark.parametrize("missing", [True, False], ids=["missing", "lagging"])
def test_concurrent_terminal_reconciliation(args, batch, monkeypatch, missing):
    run = service.stage_and_run_batch(**args, batch=batch)
    attempt = run.attempt
    staged = attempt.batch
    original = _raw_snapshot(staged)
    ledger = list(attempt.results.order_by("pk").values())
    if missing:
        run.delete()
    else:
        PipelineRun.objects.filter(pk=run.pk).update(status="running", finished_at=None)

    # Both callers must reach independent reconstruction before either can finalize.
    barrier = Barrier(2, timeout=10)
    load = service.load_source_batch

    def synchronized_read(**kwargs):
        barrier.wait()
        return load(**kwargs)

    monkeypatch.setattr(service, "load_source_batch", synchronized_read)

    def worker():
        connection = connections["default"]
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '10s'")
                cursor.execute("SELECT pg_backend_pid()")
                pid = cursor.fetchone()[0]
            result = service.run_staged_batch(**args, batch_number=0)
            return pid, result
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        workers = [future.result(timeout=30) for future in futures]
    assert len({pid for pid, _ in workers}) == 2
    persisted = PipelineRun.objects.get(attempt=attempt)
    if not missing:
        assert persisted.pk == run.pk
    for _, result in workers:
        assert (result.pk, result.updated_at) == (persisted.pk, persisted.updated_at)
        assert (result.status, result.started_at, result.finished_at) == (
            "succeeded",
            attempt.started_at,
            attempt.finished_at,
        )
    assert TransformationAttempt.objects.get(pk=attempt.pk).updated_at == attempt.updated_at
    assert list(attempt.results.order_by("pk").values()) == ledger
    assert _raw_snapshot(staged) == original


def test_reverse_loses_only_link_and_reapply_leaves_it_null(args, batch):
    run = service.stage_and_run_batch(**args, batch=batch)
    connection = connections["default"]
    assert connection.settings_dict["NAME"].startswith("test_dataxai_staging_")
    try:
        MigrationExecutor(connection).migrate([("etl", "0002_staging_schema")])
        assert (
            PipelineRun.objects.filter(pk=run.pk).values_list("status", flat=True).get()
            == "succeeded"
        )
        assert TransformationAttempt.objects.get(pk=run.attempt_id).results.count() == 3
    finally:
        MigrationExecutor(connection).migrate([("etl", "0003_pipelinerun_attempt")])
    run.refresh_from_db()
    assert run.attempt_id is None
