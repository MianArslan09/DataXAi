"""PostgreSQL readback integrity using synthetic batches and disposable databases."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pandas as pd
import pytest
from django.db import OperationalError, connections
from django.db.models.query import QuerySet
from django.test.utils import CaptureQueriesContext
from pandas.testing import assert_frame_equal

from etl import staging_reader
from etl.exceptions import StagingBatchNotFoundError, StagingIntegrityError
from etl.models import StagingBatch, StagingIngestion, StagingRecord
from etl.serialization import encode_source_batch, fingerprint_source_batch
from etl.staging import stage_source_batch
from etl.tests.test_staging_constraints import staging_postgres as staging_postgres
from etl.tests.test_staging_persistence import application_postgres as application_postgres
from etl.tests.test_staging_persistence import batch as batch
from etl.tests.test_staging_persistence import ingestion as ingestion


def _read(ingestion, number=0):
    return staging_reader.load_source_batch(ingestion_id=ingestion.pk, batch_number=number)


def _raw_snapshot(staged):
    """Bypass field decoding so even invalid stored JSON can be compared unchanged."""
    with connections["default"].cursor() as cursor:
        cursor.execute("SELECT * FROM etl_stagingbatch WHERE id = %s", [staged.pk])
        header = cursor.fetchone()
        cursor.execute(
            "SELECT * FROM etl_stagingrecord WHERE batch_id = %s ORDER BY row_position",
            [staged.pk],
        )
        return header, cursor.fetchall()


def _assert_read_only(queries):
    assert queries
    for query in queries:
        sql = query["sql"].lstrip().upper()
        assert not sql.startswith(("INSERT", "UPDATE", "DELETE"))
        assert "FOR UPDATE" not in sql and "FOR SHARE" not in sql


def test_exact_readback_is_fresh_read_only_and_fetches_bounded_chunks(
    ingestion, batch, monkeypatch
):
    staged = stage_source_batch(ingestion_id=ingestion.pk, batch=batch)
    before = _raw_snapshot(staged)
    original_iterator = QuerySet.iterator
    chunk_sizes = []

    def observed_iterator(query, *args, **kwargs):
        if query.model is StagingRecord:
            assert query.db == "default"
            chunk_sizes.append(kwargs["chunk_size"])
            assert 0 < kwargs["chunk_size"] <= 1000
        return original_iterator(query, *args, **kwargs)

    monkeypatch.setattr(QuerySet, "iterator", observed_iterator)
    with CaptureQueriesContext(connections["default"]) as queries:
        first = _read(ingestion)
        second = _read(ingestion)
    _assert_read_only(queries)
    assert chunk_sizes == [1000, 1000]
    assert first is not second and first.data is not second.data
    for result in (first, second):
        assert_frame_equal(result.data, batch.data, check_exact=True)
        assert encode_source_batch(result) == encode_source_batch(batch)
        assert result.source_name == batch.source_name
        assert result.batch_number == batch.batch_number
        assert result.extracted_at == batch.extracted_at
        assert result.data.iloc[0, 5].as_tuple() == Decimal("-0.0100").as_tuple()
        assert result.data.iloc[0, 4].nanosecond == 789
        assert result.data.duplicated().tolist() == [False, True, True]
        assert result.data.index.tolist() == ["same\x00index"] * 3
        assert "\x00" in result.source_name and "\x00" in result.data.columns[-1]
    first.data.iloc[0, 0] = "caller mutation"
    assert second.data.iloc[0, 0] == batch.data.iloc[0, 0] == "0001"
    assert _raw_snapshot(staged) == before
    assert_frame_equal(_read(ingestion).data, batch.data, check_exact=True)


def test_nullable_dtypes_and_nondefault_range_index_round_trip(ingestion, batch):
    frame = batch.data.copy(deep=True)
    frame["Quantity"] = pd.array([-2, None, 3], dtype="Int64")
    frame["Description"] = pd.array(["Urdu \u0627\u0631\u062f\u0648\x00", None, ""], dtype="string")
    frame["InvoiceDate"] = pd.array(
        [pd.Timestamp("2026-10-20 14:30:00.123456789"), pd.NaT, pd.Timestamp("2026-10-21")],
        dtype="datetime64[ns]",
    )
    frame["flag"] = pd.array([True, None, False], dtype="boolean")
    frame.index = pd.RangeIndex(4, 10, 2, name="position\x00name")
    source = replace(batch, data=frame)
    stage_source_batch(ingestion_id=ingestion.pk, batch=source)
    result = _read(ingestion)
    assert_frame_equal(result.data, frame, check_exact=True)
    assert encode_source_batch(result) == encode_source_batch(source)


def test_equal_content_batch_numbers_load_their_own_provenance(ingestion, batch):
    first = stage_source_batch(ingestion_id=ingestion.pk, batch=batch)
    next_batch = replace(batch, batch_number=1, extracted_at=batch.extracted_at + timedelta(days=1))
    second = stage_source_batch(ingestion_id=ingestion.pk, batch=next_batch)
    assert first.pk != second.pk and first.content_hash == second.content_hash
    assert encode_source_batch(_read(ingestion, 0)) == encode_source_batch(batch)
    assert encode_source_batch(_read(ingestion, 1)) == encode_source_batch(next_batch)


@pytest.mark.parametrize("missing_ingestion", [False, True])
def test_absent_identity_is_not_found(ingestion, missing_ingestion):
    identity = uuid4() if missing_ingestion else ingestion.pk
    with CaptureQueriesContext(connections["default"]) as queries:
        with pytest.raises(StagingBatchNotFoundError):
            staging_reader.load_source_batch(ingestion_id=identity, batch_number=0)
    _assert_read_only(queries)


def test_invalid_identity_is_rejected_without_querying(ingestion):
    with CaptureQueriesContext(connections["default"]) as queries:
        for identity in (None, str(ingestion.pk), 1):
            with pytest.raises(StagingIntegrityError):
                staging_reader.load_source_batch(ingestion_id=identity, batch_number=0)
        for number in (-1, True, 0.0, "0", None):
            with pytest.raises(StagingIntegrityError):
                staging_reader.load_source_batch(ingestion_id=ingestion.pk, batch_number=number)
    assert not queries


def test_application_postgresql_is_required(ingestion, monkeypatch):
    monkeypatch.setattr(connections["default"], "vendor", "sqlite")
    with pytest.raises(StagingIntegrityError, match="PostgreSQL"):
        _read(ingestion)


@pytest.mark.parametrize("status", ["OPEN", "COMPLETE", "ABORTED"])
def test_readback_is_independent_of_ingestion_status(ingestion, batch, status):
    stage_source_batch(ingestion_id=ingestion.pk, batch=batch)
    changes = {"status": status}
    if status == "COMPLETE":
        changes.update(
            completed_at=datetime.now(timezone.utc), final_batch_count=1, final_row_count=len(batch)
        )
    elif status == "ABORTED":
        changes["aborted_at"] = datetime.now(timezone.utc)
    StagingIngestion.objects.filter(pk=ingestion.pk).update(**changes)
    with CaptureQueriesContext(connections["default"]) as queries:
        result = _read(ingestion)
    _assert_read_only(queries)
    assert encode_source_batch(result) == encode_source_batch(batch)
    ingestion.refresh_from_db()
    assert ingestion.status == status


def test_empty_batch_preserves_structure_and_metadata(ingestion, batch):
    empty = replace(batch, data=batch.data.iloc[:0].copy(), source_name="")
    staged = stage_source_batch(ingestion_id=ingestion.pk, batch=empty)
    result = _read(ingestion)
    assert_frame_equal(result.data, empty.data, check_exact=True)
    assert encode_source_batch(result) == encode_source_batch(empty)
    assert staged.records.count() == 0


@pytest.mark.parametrize(
    "damage",
    [
        "missing_record",
        "position",
        "row_count",
        "source_name",
        "header_row_count",
        "extracted_at",
        "batch_number",
        "version",
        "embedded_records",
        "hash",
        "record_tag",
        "encoded_position",
        "record_shape",
    ],
)
def test_corruption_fails_without_partial_result_or_mutation(ingestion, batch, damage):
    staged = stage_source_batch(ingestion_id=ingestion.pk, batch=batch)
    rows = staged.records.order_by("row_position")
    first = rows.first()
    if damage == "missing_record":
        first.delete()
    elif damage == "position":
        rows.filter(pk=first.pk).update(row_position=10)
    elif damage == "row_count":
        # Agreeing relational/header counts still cannot hide an incomplete record set.
        StagingBatch.objects.filter(pk=staged.pk).update(
            row_count=4, header={**staged.header, "row_count": 4}
        )
    elif damage in {
        "source_name",
        "header_row_count",
        "extracted_at",
        "batch_number",
        "version",
        "embedded_records",
    }:
        header = deepcopy(staged.header)
        key, value = {
            "source_name": ("source_name", "synthetic-private-value"),
            "header_row_count": ("row_count", 4),
            "extracted_at": ("extracted_at", {"type": "synthetic-private-value"}),
            "batch_number": ("batch_number", 9),
            "version": ("version", 2),
            "embedded_records": ("records", []),
        }[damage]
        header[key] = value
        StagingBatch.objects.filter(pk=staged.pk).update(header=header)
    elif damage == "hash":
        StagingBatch.objects.filter(pk=staged.pk).update(content_hash="0" * 64)
    else:
        payload = deepcopy(first.record_payload)
        if damage == "record_tag":
            payload["values"][0] = {"type": "synthetic-private-value"}
        elif damage == "encoded_position":
            payload["row_position"] = 2
        else:
            payload.pop("values")
        rows.filter(pk=first.pk).update(record_payload=payload)
    before = _raw_snapshot(staged)
    sentinel = object()
    result = sentinel
    with CaptureQueriesContext(connections["default"]) as queries:
        with pytest.raises(StagingIntegrityError) as caught:
            result = _read(ingestion)
    _assert_read_only(queries)
    assert result is sentinel
    assert "synthetic-private-value" not in str(caught.value)
    assert len(str(caught.value)) < 300
    assert _raw_snapshot(staged) == before


@pytest.mark.parametrize("location", ["header", "record"])
def test_invalid_escaped_storage_is_integrity_error(ingestion, batch, location):
    staged = stage_source_batch(ingestion_id=ingestion.pk, batch=batch)
    # PostgreSQL accepts duplicate JSON keys; the strict escaped-text reader rejects them.
    with connections["default"].cursor() as cursor:
        if location == "header":
            cursor.execute(
                "UPDATE etl_stagingbatch SET header = %s WHERE id = %s",
                ['{"source_name":"synthetic-private-value","source_name":"duplicate"}', staged.pk],
            )
        else:
            cursor.execute(
                "UPDATE etl_stagingrecord SET record_payload = %s WHERE id = %s",
                ['{"values":[],"values":["synthetic-private-value"]}', staged.records.first().pk],
            )
    before = _raw_snapshot(staged)
    with CaptureQueriesContext(connections["default"]) as queries:
        with pytest.raises(StagingIntegrityError) as caught:
            _read(ingestion)
    _assert_read_only(queries)
    assert "synthetic-private-value" not in str(caught.value)
    assert _raw_snapshot(staged) == before


def test_valid_extraction_timestamp_is_provenance_not_hashed_content(ingestion, batch):
    staged = stage_source_batch(ingestion_id=ingestion.pk, batch=batch)
    changed = replace(batch, extracted_at=batch.extracted_at + timedelta(days=1))
    header = {**staged.header, "extracted_at": encode_source_batch(changed)["extracted_at"]}
    StagingBatch.objects.filter(pk=staged.pk).update(header=header)
    result = _read(ingestion)
    assert result.extracted_at == changed.extracted_at
    assert fingerprint_source_batch(result) == staged.content_hash
    assert_frame_equal(result.data, batch.data, check_exact=True)


@pytest.mark.parametrize("error", [OperationalError, RuntimeError])
def test_unexpected_failures_are_not_reported_as_absent_or_corrupt(ingestion, monkeypatch, error):
    def unavailable(*args, **kwargs):
        raise error("synthetic failure")

    monkeypatch.setattr(QuerySet, "get", unavailable)
    with pytest.raises(error, match="synthetic failure"):
        _read(ingestion)
