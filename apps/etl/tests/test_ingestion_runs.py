"""Coordinator effects on the disposable default PostgreSQL connection."""

from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import transaction
from django.utils import timezone

from etl.attempt_claims import claim_attempt
from etl.exceptions import (
    AttemptConflictError,
    AttemptRegistrationError,
    StagingBatchNotFoundError,
    StagingIngestionError,
    StagingIntegrityError,
)
from etl.ingestion_runs import resume_staged_range, run_ingestion
from etl.models import PipelineRun, TransformationAttempt
from etl.sources import CsvSource, ExcelSource, PostgresSource
from etl.staging import stage_source_batch
from etl.tests.test_staging_constraints import staging_postgres as staging_postgres
from etl.tests.test_staging_persistence import application_postgres as application_postgres
from etl.tests.test_staging_persistence import batch as batch
from etl.tests.test_staging_persistence import ingestion as ingestion
from etl.transformation_attempts import register_attempt
from etl.transforms.date_contract import SourceDateProfile

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def options(ingestion):
    return dict(
        ingestion_id=ingestion.pk,
        execution_key=uuid4(),
        profile=SourceDateProfile(),
        transformation_version="coord-v1",
        claim_ttl_seconds=60,
    )


@pytest.fixture
def csv():
    return CsvSource(FIXTURES / "online_retail_ii_sample.csv", batch_size=5)


def lifecycle(ingestion):
    ingestion.refresh_from_db()
    return (
        ingestion.status,
        ingestion.final_batch_count,
        ingestion.final_row_count,
        ingestion.completed_at,
        ingestion.aborted_at,
    )


def test_live_and_staged_execution_identities(options, ingestion, csv, batch, monkeypatch):
    initial = lifecycle(ingestion)
    batch.data.iloc[0, batch.data.columns.get_loc("InvoiceDate")] = None
    first = replace(batch, source_name=f"csv:{csv.path.name}")
    monkeypatch.setattr(csv, "extract", lambda: iter([first, replace(first, batch_number=1)]))
    live = run_ingestion(**options, source=csv)
    assert csv._reader is None and live.source_exhausted
    assert (
        live.successful_batches,
        live.successful_rows,
        live.safe_rows,
        live.unresolved_rows,
    ) == (2, 6, 4, 2)
    assert ingestion.batches.values("content_hash").order_by().distinct().count() == 1
    with pytest.raises(FrozenInstanceError):
        live.safe_rows = 0
    selected = PipelineRun.objects.filter(attempt__batch__ingestion=ingestion).order_by("pk")
    original = list(selected.values_list("pk", "updated_at", "attempt__attempt_key"))
    assert len(original) == 2 and {row[2] for row in original} == {options["execution_key"]}
    monkeypatch.setattr(CsvSource, "__enter__", lambda self: pytest.fail("source reopened"))
    replay = resume_staged_range(**options, first_batch=0, last_batch=1)
    assert (replay.requested_first_batch, replay.requested_last_batch, replay.successful_rows) == (
        0,
        1,
        6,
    )
    assert (
        not replay.source_exhausted
        and list(selected.values_list("pk", "updated_at", "attempt__attempt_key")) == original
    )
    with pytest.raises(AttemptConflictError):
        resume_staged_range(
            **{**options, "transformation_version": "coord-v2"}, first_batch=0, last_batch=1
        )
    fresh = resume_staged_range(
        **{**options, "execution_key": uuid4()}, first_batch=1, last_batch=1
    )
    assert fresh.successful_rows == 3 and selected.count() == 3
    assert lifecycle(ingestion) == initial


def test_real_multisheet_scope(options, ingestion):
    ingestion.source_kind = "EXCEL"
    ingestion.save(update_fields=["source_kind"])
    source = ExcelSource(FIXTURES / "online_retail_ii_sample.xlsx", batch_size=5)
    result = run_ingestion(**options, source=source)
    assert source._workbook is None and result.source_exhausted
    assert (result.successful_batches, result.successful_rows) == (4, 18)
    assert lifecycle(ingestion) == ("OPEN", None, None, None, None)


def test_source_entry_failure_releases_partial_handle(options, csv, monkeypatch):
    enter = CsvSource.__enter__

    def interrupted(source):
        enter(source)
        raise RuntimeError("interrupted after opening")

    monkeypatch.setattr(CsvSource, "__enter__", interrupted)
    with pytest.raises(RuntimeError, match="after opening"):
        run_ingestion(**options, source=csv)
    assert csv._reader is None


def test_unexpected_suffix_cannot_report_clean_traversal(
    options, ingestion, csv, batch, monkeypatch
):
    first = replace(batch, source_name=f"csv:{csv.path.name}")

    def source():
        yield first
        stage_source_batch(ingestion_id=ingestion.pk, batch=replace(first, batch_number=1))

    monkeypatch.setattr(csv, "extract", source)
    with pytest.raises(StagingIntegrityError, match="Stored batch range"):
        run_ingestion(**options, source=csv)
    assert csv._reader is None
    assert lifecycle(ingestion) == ("OPEN", None, None, None, None)


@pytest.mark.parametrize("explicit", [False, True])
def test_empty(options, ingestion, csv, batch, monkeypatch, explicit):
    empty = replace(batch, data=batch.data.iloc[:0], source_name=f"csv:{csv.path.name}")
    monkeypatch.setattr(csv, "extract", lambda: iter([empty] if explicit else []))
    result = run_ingestion(**options, source=csv)
    assert result.source_exhausted and result.successful_rows == 0
    assert result.successful_batches == ingestion.batches.count() == int(explicit)
    assert lifecycle(ingestion) == ("OPEN", None, None, None, None)


def test_failed_run_stops_consumption(options, ingestion, csv, batch, monkeypatch):
    good = replace(batch, source_name=f"csv:{csv.path.name}")
    invalid = good.data.copy()
    invalid.iloc[0, invalid.columns.get_loc("Quantity")] = "invalid"

    def source():
        yield good
        yield replace(good, data=invalid, batch_number=1)
        pytest.fail("consumed past FAILED")

    monkeypatch.setattr(csv, "extract", source)
    stopped = run_ingestion(**options, source=csv)
    assert not stopped.source_exhausted and csv._reader is None
    assert (stopped.successful_batches, stopped.successful_rows, stopped.failed_batch_number) == (
        1,
        3,
        1,
    )
    assert PipelineRun.objects.get(pk=stopped.failed_run_id).status == "failed"
    replay = resume_staged_range(**options, first_batch=0, last_batch=1)
    assert replay.failed_run_id == stopped.failed_run_id and replay.successful_rows == 3
    assert lifecycle(ingestion) == ("OPEN", None, None, None, None)


@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt])
def test_interruption_keeps_prefix(options, ingestion, csv, batch, monkeypatch, error):
    def source():
        yield replace(batch, source_name=f"csv:{csv.path.name}")
        raise error()

    monkeypatch.setattr(csv, "extract", source)
    with pytest.raises(error):
        run_ingestion(**options, source=csv)
    assert csv._reader is None and ingestion.batches.count() == 1
    with pytest.raises(StagingIngestionError):
        run_ingestion(**options, source=csv)
    assert resume_staged_range(**options, first_batch=0, last_batch=0).successful_rows == 3


@pytest.mark.parametrize(
    "number,name", [(0, None), (2, None), (False, None), (-1, None), (1, "csv:other")]
)
def test_reject_bad_identity_after_commit(options, csv, batch, monkeypatch, number, name):
    first = replace(batch, source_name=f"csv:{csv.path.name}")
    monkeypatch.setattr(
        csv,
        "extract",
        lambda: iter(
            [first, replace(first, batch_number=number, source_name=name or first.source_name)]
        ),
    )
    with pytest.raises(StagingIntegrityError):
        run_ingestion(**options, source=csv)
    assert csv._reader is None
    assert (
        TransformationAttempt.objects.get(attempt_key=options["execution_key"]).status
        == "SUCCEEDED"
    )


def test_preflight_before_io(options, csv, ingestion):
    with csv, pytest.raises(StagingIngestionError):
        run_ingestion(**options, source=csv)
    unopened = CsvSource(FIXTURES / "absent.csv")
    for change in (
        {"execution_key": "invalid"},
        {"claim_ttl_seconds": True},
        {"profile": {}},
        {"transformation_version": ""},
    ):
        with pytest.raises(AttemptRegistrationError):
            run_ingestion(**{**options, **change}, source=unopened)
    with pytest.raises(StagingIngestionError):
        run_ingestion(**{**options, "ingestion_id": uuid4()}, source=unopened)
    with pytest.raises(StagingIngestionError):
        run_ingestion(**options, source=PostgresSource("unused"))
    with transaction.atomic(), pytest.raises(AttemptRegistrationError):
        run_ingestion(**options, source=unopened)
    with transaction.atomic(), pytest.raises(AttemptRegistrationError):
        resume_staged_range(**options, first_batch=0, last_batch=0)
    assert not ingestion.batches.exists()


@pytest.mark.parametrize("first,last", [(False, 0), (0, True), (-1, 0), (1, 0), (0, 2**63)])
def test_invalid_bounds(options, first, last):
    with pytest.raises(AttemptRegistrationError):
        resume_staged_range(**options, first_batch=first, last_batch=last)


def test_gap_before_admission_and_corrupt_later_batch(options, ingestion, batch):
    staged = [
        stage_source_batch(ingestion_id=ingestion.pk, batch=replace(batch, batch_number=i))
        for i in range(3)
    ]
    ingestion.batches.filter(pk=staged[1].pk).update(batch_number=3)
    with pytest.raises(StagingBatchNotFoundError):
        resume_staged_range(**options, first_batch=0, last_batch=2)
    assert not TransformationAttempt.objects.filter(attempt_key=options["execution_key"]).exists()
    ingestion.batches.filter(pk=staged[1].pk).update(batch_number=1, content_hash="0" * 64)
    with pytest.raises(StagingIntegrityError):
        resume_staged_range(**options, first_batch=0, last_batch=2)
    assert staged[0].attempts.get().status == "SUCCEEDED" and not staged[2].attempts.exists()


@pytest.mark.parametrize("state", ["ABORTED", "COMPLETE"])
def test_closed_ingestion_policy(options, ingestion, batch, csv, state):
    stage_source_batch(ingestion_id=ingestion.pk, batch=batch)
    ingestion.status = state
    if state == "COMPLETE":
        ingestion.completed_at = timezone.now()
        ingestion.final_row_count, ingestion.final_batch_count = 3, 1
    else:
        ingestion.aborted_at = timezone.now()
    ingestion.save()
    before = lifecycle(ingestion)
    with pytest.raises(StagingIngestionError):
        run_ingestion(**options, source=csv)
    if state == "ABORTED":
        with pytest.raises(StagingIngestionError):
            resume_staged_range(**options, first_batch=0, last_batch=0)
    else:
        assert resume_staged_range(**options, first_batch=0, last_batch=0).successful_rows == 3
    assert before == lifecycle(ingestion)


def test_claim_conflict_then_explicit_recovery(options, ingestion, batch):
    stage_source_batch(ingestion_id=ingestion.pk, batch=batch)
    attempt = register_attempt(
        ingestion_id=ingestion.pk,
        batch_number=0,
        attempt_key=options["execution_key"],
        profile=options["profile"],
        transformation_version=options["transformation_version"],
    )
    token = claim_attempt(attempt_id=attempt.pk, ttl_seconds=60)
    with pytest.raises(AttemptConflictError):
        resume_staged_range(**options, first_batch=0, last_batch=0)
    TransformationAttempt.objects.filter(pk=attempt.pk).update(
        claim_expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert resume_staged_range(**options, first_batch=0, last_batch=0).successful_batches == 1
    attempt.refresh_from_db()
    assert attempt.claim_generation == token.generation + 1 and attempt.status == "SUCCEEDED"
