"""Staging schema contracts; these tests do not implement a staging loader."""

import json
from datetime import datetime, timedelta
from datetime import timezone as datetime_timezone
from decimal import Decimal
from uuid import UUID

import pandas as pd
import pytest
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from pandas.testing import assert_frame_equal

from accounts.models import User
from etl.models import (
    PipelineRun,
    StagingBatch,
    StagingIngestion,
    StagingRecord,
    TransformationAttempt,
    TransformationRecordResult,
)
from etl.serialization import decode_source_batch, encode_source_batch, fingerprint_source_batch
from etl.sources.contract import CANONICAL_COLUMNS, SourceBatch

pytestmark = pytest.mark.django_db


def test_storage_envelope_preserves_python_values_without_mutation():
    header = {"source_name": "source\x00\\u0000", "columns": ["synthetic\x00column"]}
    original = {"source_name": header["source_name"], "columns": header["columns"].copy()}
    for model, field_name, value in (
        (StagingBatch, "header", header),
        (StagingBatch, "source_name", 'source\x00\\u0000"'),
        (StagingRecord, "record_payload", {"values": ["synthetic\x00value"]}),
    ):
        field = model._meta.get_field(field_name)
        stored = field.get_prep_value(value)
        assert stored.isascii() and "\x00" not in stored
        assert json.loads(stored) == value
        assert field.from_db_value(stored, None, None) == value
        with pytest.raises(ValidationError, match="Invalid staging JSON type"):
            field.get_prep_value([])
    assert header == original


@pytest.mark.parametrize("stored", ['{"x":', "[]", "null", '{"x":1,"x":2}', '{"x":NaN}'])
def test_storage_decoder_rejects_invalid_json_without_echoing_values(stored):
    field = StagingRecord._meta.get_field("record_payload")
    with pytest.raises(ValidationError) as caught:
        field.from_db_value(stored, None, None)
    assert caught.value.code == "invalid_staging_json"
    assert stored not in str(caught.value)


@pytest.fixture
def source_batch():
    """Synthetic duplicate transactions with representation-sensitive source values."""
    row = [
        "00123",
        "POST",
        None,
        -2,
        pd.Timestamp("2026-10-20 14:30:00.123456789"),
        Decimal("-0.0100"),
        "00007",
        "Pakistan",
    ]
    frame = pd.DataFrame(
        [row, row.copy()], columns=CANONICAL_COLUMNS, index=["duplicate", "duplicate"], dtype=object
    )
    return SourceBatch(
        frame,
        source_name="synthetic-source",
        batch_number=0,
        extracted_at=datetime(2026, 10, 20, tzinfo=datetime_timezone.utc),
    )


@pytest.fixture
def staged_batch(source_batch):
    payload = encode_source_batch(source_batch)
    ingestion = StagingIngestion.objects.create(
        source_kind=StagingIngestion.SourceKind.CSV, source_reference="synthetic-import-001"
    )
    batch = StagingBatch.objects.create(
        ingestion=ingestion,
        batch_number=source_batch.batch_number,
        source_name=source_batch.source_name,
        header={key: value for key, value in payload.items() if key != "records"},
        row_count=len(source_batch),
        content_hash=fingerprint_source_batch(source_batch),
    )
    for record in payload["records"]:
        StagingRecord.objects.create(
            batch=batch, row_position=record["row_position"], record_payload=record
        )
    return batch


@pytest.fixture
def attempt(staged_batch):
    return TransformationAttempt.objects.create(
        batch=staged_batch,
        configuration_hash="a" * 64,
        transformation_version="dates-and-business-rules-v1",
    )


def test_ingestion_defaults_and_independent_uuid_keys():
    first = StagingIngestion.objects.create(source_kind="CSV", source_reference="import-001")
    second = StagingIngestion.objects.create(source_kind="EXCEL", source_reference="import-002")
    assert isinstance(first.pk, UUID)
    assert first.pk != second.pk
    assert first.status == StagingIngestion.Status.OPEN
    assert first.initiated_by is None
    assert first.final_batch_count is first.final_row_count is None
    assert first.completed_at is first.aborted_at is None
    assert first.created_at is not None and first.updated_at is not None
    first.full_clean()


@pytest.mark.parametrize("source_kind", ["CSV", "EXCEL", "POSTGRES"])
def test_all_approved_source_kinds(source_kind):
    instance = StagingIngestion(source_kind=source_kind, source_reference="source-001")
    instance.full_clean()
    instance.save()
    instance.refresh_from_db()
    assert instance.source_kind == source_kind


@pytest.mark.parametrize("status", ["OPEN", "COMPLETE", "ABORTED"])
def test_ingestion_permitted_states(status):
    instance = StagingIngestion(source_kind="CSV", source_reference="source-001", status=status)
    if status == "COMPLETE":
        instance.final_batch_count = 0
        instance.final_row_count = 0
        instance.completed_at = timezone.now()
    elif status == "ABORTED":
        instance.aborted_at = timezone.now()
    instance.full_clean()
    instance.save()
    instance.refresh_from_db()
    assert instance.status == status


@pytest.mark.parametrize(
    "source_reference",
    [
        "",
        "x" * 256,
        "postgresql://synthetic:password@example.invalid/source",
        "D:\\imports\\orders.csv",
        "/imports/orders.csv",
        "source\nsecond-line",
        "source?token=synthetic",
    ],
)
def test_source_reference_rejects_unsanitized_or_oversized_values(source_reference):
    instance = StagingIngestion(source_kind="CSV", source_reference=source_reference)
    with pytest.raises(ValidationError):
        instance.full_clean()


def test_initiating_user_deletion_preserves_ingestion():
    user = User.objects.create_user(username="staging-initiator")
    instance = StagingIngestion.objects.create(
        source_kind="POSTGRES", source_reference="upstream-table-001", initiated_by=user
    )
    user.delete()
    instance.refresh_from_db()
    assert instance.initiated_by is None
    assert instance.status == StagingIngestion.Status.OPEN


def test_staging_defaults_and_conventional_primary_keys(staged_batch, attempt):
    assert staged_batch.serializer_version == 1
    assert staged_batch.batch_number == 0
    assert staged_batch.created_at is not None
    assert isinstance(attempt.attempt_key, UUID)
    assert attempt.status == TransformationAttempt.Status.REGISTERED
    assert attempt.configuration == {}
    assert attempt.claim_generation == 0
    assert attempt.claim_owner is attempt.claim_expires_at is None
    assert attempt.safe_row_count is attempt.unresolved_row_count is None
    assert attempt.failure_code == attempt.failure_stage == ""
    assert attempt.failure_diagnostics is None
    assert attempt.started_at is attempt.finished_at is None
    assert attempt.registered_at is not None and attempt.updated_at is not None
    for model in (StagingBatch, StagingRecord, TransformationAttempt, TransformationRecordResult):
        assert isinstance(model._meta.pk, models.BigAutoField)
    staged_batch.full_clean()
    attempt.full_clean()


def test_independent_attempt_keys_and_configuration_defaults(staged_batch, attempt):
    second = TransformationAttempt.objects.create(
        batch=staged_batch, configuration_hash="b" * 64, transformation_version="v1"
    )
    assert attempt.attempt_key != second.attempt_key
    attempt.configuration["profile"] = "synthetic"
    assert second.configuration == {}


def test_json_storage_reassembles_exact_ser_v1_without_deduplication(staged_batch, source_batch):
    staged_batch.refresh_from_db()
    rows = list(StagingRecord.objects.filter(batch=staged_batch).order_by("row_position"))
    assert [row.row_position for row in rows] == [0, 1]
    payload = {**staged_batch.header, "records": [row.record_payload for row in rows]}
    assert payload == encode_source_batch(source_batch)
    reconstructed = decode_source_batch(payload)
    assert_frame_equal(reconstructed.data, source_batch.data, check_exact=True)
    assert reconstructed.data.iloc[0]["Price"].as_tuple() == Decimal("-0.0100").as_tuple()
    assert reconstructed.source_name == source_batch.source_name
    assert reconstructed.batch_number == source_batch.batch_number
    assert reconstructed.extracted_at == source_batch.extracted_at
    assert fingerprint_source_batch(reconstructed) == staged_batch.content_hash
    assert len(reconstructed) == staged_batch.row_count == 2
    assert reconstructed.data.duplicated().tolist() == [False, True]
    assert "records" not in staged_batch.header


@pytest.mark.parametrize("status", ["REGISTERED", "RUNNING", "SUCCEEDED", "FAILED"])
def test_attempt_permitted_states(staged_batch, status):
    instance = TransformationAttempt(
        batch=staged_batch,
        configuration_hash="a" * 64,
        transformation_version="v1",
        status=status,
    )
    now = timezone.now()
    if status in {"RUNNING", "SUCCEEDED"}:
        instance.started_at = now
    if status in {"SUCCEEDED", "FAILED"}:
        instance.finished_at = now
    if status == "SUCCEEDED":
        instance.safe_row_count, instance.unresolved_row_count = 1, 1
    if status == "FAILED":
        instance.failure_code = "SYNTHETIC_FAILURE"
        instance.failure_stage = "date-interpretation"
        instance.failure_diagnostics = {"reason": "synthetic test failure"}
    instance.full_clean()
    instance.save()
    instance.refresh_from_db()
    assert instance.status == status


def test_claim_metadata_storage_does_not_claim_or_execute_work(attempt):
    started = timezone.now()
    attempt.status = TransformationAttempt.Status.RUNNING
    attempt.started_at = started
    attempt.claim_owner = "synthetic-worker"
    attempt.claim_generation = 1
    attempt.claim_expires_at = started + timedelta(minutes=5)
    attempt.full_clean()
    attempt.save()
    attempt.refresh_from_db()
    assert attempt.claim_generation == 1
    assert attempt.claim_expires_at == started + timedelta(minutes=5)
    assert attempt.finished_at is None
    assert not TransformationRecordResult.objects.filter(attempt=attempt).exists()
    attempt.batch.ingestion.refresh_from_db()
    assert attempt.batch.ingestion.status == StagingIngestion.Status.OPEN


@pytest.mark.parametrize("outcome", ["SAFE", "UNRESOLVED"])
def test_result_outcomes_and_nullable_payloads(staged_batch, attempt, outcome):
    record = StagingRecord.objects.get(batch=staged_batch, row_position=0)
    result = TransformationRecordResult(
        batch=staged_batch, attempt=attempt, staged_record=record, outcome=outcome
    )
    result.full_clean()
    result.save()
    result.refresh_from_db()
    assert result.outcome == outcome
    assert result.result_payload is result.interpretation_evidence is None
    assert result.batch_id == result.attempt.batch_id == result.staged_record.batch_id


def test_result_json_evidence_is_separate_from_original_record(staged_batch, attempt):
    record = StagingRecord.objects.get(batch=staged_batch, row_position=0)
    original = record.record_payload
    result = TransformationRecordResult.objects.create(
        batch=staged_batch,
        attempt=attempt,
        staged_record=record,
        outcome="UNRESOLVED",
        result_payload={"synthetic": True, "date": None},
        interpretation_evidence={"issues": [{"code": "MISSING_DATE", "row_position": 0}]},
    )
    result.refresh_from_db()
    record.refresh_from_db()
    assert result.result_payload == {"synthetic": True, "date": None}
    assert result.interpretation_evidence["issues"][0]["code"] == "MISSING_DATE"
    assert record.record_payload == original


@pytest.mark.parametrize("target", ["ingestion", "batch", "attempt", "record"])
def test_protected_staging_relationships(staged_batch, attempt, target):
    record = StagingRecord.objects.get(batch=staged_batch, row_position=0)
    result = TransformationRecordResult.objects.create(
        batch=staged_batch, attempt=attempt, staged_record=record, outcome="SAFE"
    )
    protected = {
        "ingestion": staged_batch.ingestion,
        "batch": staged_batch,
        "attempt": attempt,
        "record": record,
    }[target]
    with pytest.raises(ProtectedError):
        protected.delete()
    assert TransformationRecordResult.objects.filter(pk=result.pk).exists()
    assert StagingRecord.objects.filter(batch=staged_batch).count() == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("configuration_hash", "a" * 63),
        ("configuration_hash", "A" * 64),
        ("configuration_hash", "g" * 64),
        ("configuration", {"synthetic": "x" * 16385}),
        ("configuration", ["not-an-object"]),
        ("transformation_version", "v" * 129),
        ("status", "UNKNOWN"),
        ("claim_generation", -1),
        ("claim_owner", "x" * 129),
        ("failure_code", "x" * 65),
        ("failure_stage", "x" * 65),
        ("failure_diagnostics", {"synthetic": "x" * 4097}),
    ],
)
def test_attempt_model_validation_rejects_invalid_metadata(attempt, field, value):
    if field.startswith("failure_"):
        attempt.status = TransformationAttempt.Status.FAILED
        attempt.finished_at = timezone.now()
    if field == "claim_owner":
        attempt.claim_owner = "synthetic-worker"
        attempt.claim_generation = 1
        attempt.claim_expires_at = timezone.now() + timedelta(minutes=5)
    attempt.full_clean()
    setattr(attempt, field, value)
    with pytest.raises(ValidationError):
        attempt.full_clean()


@pytest.mark.parametrize("content_hash", ["", "a" * 63, "A" * 64, "g" * 64])
def test_batch_hash_model_validation(staged_batch, content_hash):
    staged_batch.content_hash = content_hash
    with pytest.raises(ValidationError):
        staged_batch.full_clean()


def test_new_schema_does_not_change_pipeline_run_behavior(staged_batch):
    started = timezone.now()
    run = PipelineRun.objects.create(started_at=started)
    assert run.status == PipelineRun.Status.PENDING
    assert run.fault_counts == run.cdqi_scores == {}
    assert run.records_ingested == run.records_healed == run.records_quarantined == 0
    assert run.triggered_by is None
    assert run.duration_seconds is None
    run.finished_at = started + timedelta(seconds=42)
    run.save()
    run.refresh_from_db()
    assert run.duration_seconds == 42
    staged_batch.ingestion.refresh_from_db()
    assert staged_batch.ingestion.status == StagingIngestion.Status.OPEN
    assert StagingBatch.objects.filter(pk=staged_batch.pk).exists()
