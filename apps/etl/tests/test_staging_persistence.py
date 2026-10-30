"""Real PostgreSQL effects, locking and rollback; no source database writes."""

import os
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from queue import Queue
from threading import Barrier
from uuid import uuid4

import pandas as pd
import pytest
from django.db import connections, transaction
from django.db.models.query import QuerySet
from django.test.utils import CaptureQueriesContext
from pandas.testing import assert_frame_equal

from etl import staging
from etl.exceptions import StagingConflictError, StagingIngestionError, StagingIntegrityError
from etl.models import StagingBatch, StagingIngestion, StagingRecord
from etl.serialization import decode_source_batch, encode_source_batch, fingerprint_source_batch
from etl.sources.contract import CANONICAL_COLUMNS, SourceBatch
from etl.tests.test_staging_constraints import staging_postgres


@pytest.fixture(scope="module", autouse=True)
def application_postgres(request):
    """Reuse the accepted disposable DB, temporarily as Django's default connection.

    Only in-process test configuration is swapped. Worker threads get independent
    connections to the same disposable database. Required PostgreSQL never skips.
    """
    old_required = os.environ.get("DATAXAI_REQUIRE_STAGING_POSTGRES")
    os.environ["DATAXAI_REQUIRE_STAGING_POSTGRES"] = "1"
    try:
        alias = request.getfixturevalue(staging_postgres.__name__)
    finally:
        if old_required is None:
            os.environ.pop("DATAXAI_REQUIRE_STAGING_POSTGRES", None)
        else:
            os.environ["DATAXAI_REQUIRE_STAGING_POSTGRES"] = old_required
    original_connection = connections["default"]
    original_settings = connections.databases["default"]
    connections.databases["default"] = deepcopy(connections.databases[alias])
    connections["default"] = connections[alias]
    try:
        assert connections["default"].vendor == "postgresql"
        yield
    finally:
        connections["default"] = original_connection
        connections.databases["default"] = original_settings


@pytest.fixture
def ingestion():
    return StagingIngestion.objects.create(
        source_kind="CSV", source_reference="synthetic-persistence"
    )


@pytest.fixture
def batch():
    row = [
        "0001",
        "POST",
        "synthetic \u0627\u0631\u062f\u0648\x00",
        -2,
        pd.Timestamp("2026-10-20 14:30:00.123456789"),
        Decimal("-0.0100"),
        None,
        "Pakistan",
        "extra\x00value",
    ]
    frame = pd.DataFrame(
        [row, row.copy(), row.copy()],
        columns=[*CANONICAL_COLUMNS, "extra\x00column"],
        index=["same\x00index"] * 3,
        dtype=object,
    )
    frame.index.name = "index\x00name"
    frame.columns.name = "columns\x00name"
    return SourceBatch(frame, "source\x00name", 0, datetime(2026, 10, 20, tzinfo=timezone.utc))


def _payload(staged):
    staged.refresh_from_db()
    return {
        **staged.header,
        "records": list(
            StagingRecord.objects.filter(batch=staged)
            .order_by("row_position")
            .values_list("record_payload", flat=True)
        ),
    }


def test_atomic_persistence_preserves_ser_v1_and_uses_bounded_inserts(
    ingestion, batch, monkeypatch
):
    original_frame = batch.data.copy(deep=True)
    original_updated_at = ingestion.updated_at
    original_bulk_create = QuerySet.bulk_create
    insert_sizes = []

    def observed_insert(query, objects, **kwargs):
        assert query.model is StagingRecord
        assert query.db == "default"
        assert connections["default"].in_atomic_block
        insert_sizes.append(len(objects))
        assert kwargs["batch_size"] == 2
        return original_bulk_create(query, objects, **kwargs)

    monkeypatch.setattr(staging, "_INSERT_BATCH_SIZE", 2)
    monkeypatch.setattr(QuerySet, "bulk_create", observed_insert)
    persisted = staging.stage_source_batch(ingestion_id=ingestion.pk, batch=batch)
    assert insert_sizes == [2, 1]
    assert _payload(persisted) == encode_source_batch(batch)
    reconstructed = decode_source_batch(_payload(persisted))
    assert_frame_equal(reconstructed.data, batch.data, check_exact=True)
    assert_frame_equal(batch.data, original_frame, check_exact=True)
    assert reconstructed.data.iloc[0, 5].as_tuple() == Decimal("-0.0100").as_tuple()
    assert reconstructed.data.duplicated().tolist() == [False, True, True]
    assert reconstructed.source_name == batch.source_name
    assert reconstructed.batch_number == batch.batch_number
    assert reconstructed.extracted_at == batch.extracted_at
    assert persisted.content_hash == fingerprint_source_batch(reconstructed)
    assert list(
        persisted.records.order_by("row_position").values_list("row_position", flat=True)
    ) == [0, 1, 2]
    ingestion.refresh_from_db()
    assert ingestion.status == "OPEN" and ingestion.updated_at == original_updated_at
    assert ingestion.final_batch_count is ingestion.final_row_count is None


@pytest.mark.parametrize("terminal_status", [None, "COMPLETE", "ABORTED"])
def test_lost_confirmation_retry_retains_original_provenance(ingestion, batch, terminal_status):
    committed = staging.stage_source_batch(ingestion_id=ingestion.pk, batch=batch)
    original_payload, original_created_at = _payload(committed), committed.created_at
    if terminal_status == "COMPLETE":
        StagingIngestion.objects.filter(pk=ingestion.pk).update(
            status="COMPLETE",
            completed_at=datetime.now(timezone.utc),
            final_batch_count=1,
            final_row_count=3,
        )
    elif terminal_status == "ABORTED":
        StagingIngestion.objects.filter(pk=ingestion.pk).update(
            status="ABORTED", aborted_at=datetime.now(timezone.utc)
        )
    # Simulate the caller not receiving a successful commit confirmation.
    replay = replace(batch, extracted_at=batch.extracted_at + timedelta(days=1))
    with CaptureQueriesContext(connections["default"]) as queries:
        reused = staging.stage_source_batch(ingestion_id=ingestion.pk, batch=replay)
    assert reused.pk == committed.pk and reused.created_at == original_created_at
    assert _payload(reused) == original_payload
    assert reused.header["extracted_at"] != encode_source_batch(replay)["extracted_at"]
    assert StagingBatch.objects.filter(ingestion=ingestion).count() == 1
    assert reused.records.count() == 3
    assert not any(
        q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries
    )
    if terminal_status:
        with pytest.raises(StagingIngestionError):
            staging.stage_source_batch(
                ingestion_id=ingestion.pk, batch=replace(batch, batch_number=1)
            )
        assert StagingBatch.objects.filter(ingestion=ingestion).count() == 1


def test_conflicting_retry_preserves_existing_content(ingestion, batch):
    committed = staging.stage_source_batch(ingestion_id=ingestion.pk, batch=batch)
    original = _payload(committed)
    changed = batch.data.copy(deep=True)
    changed.iloc[0, 2] = "different synthetic value"
    with pytest.raises(StagingConflictError) as caught:
        staging.stage_source_batch(ingestion_id=ingestion.pk, batch=replace(batch, data=changed))
    assert "different synthetic value" not in str(caught.value)
    assert _payload(committed) == original
    assert StagingBatch.objects.filter(ingestion=ingestion).count() == 1


def test_sequence_and_independent_ingestion_identity(ingestion, batch):
    with pytest.raises(StagingIntegrityError):
        staging.stage_source_batch(ingestion_id=ingestion.pk, batch=replace(batch, batch_number=1))
    first = staging.stage_source_batch(ingestion_id=ingestion.pk, batch=batch)
    with pytest.raises(StagingIntegrityError):
        staging.stage_source_batch(ingestion_id=ingestion.pk, batch=replace(batch, batch_number=2))
    second = staging.stage_source_batch(
        ingestion_id=ingestion.pk, batch=replace(batch, batch_number=1)
    )
    other = StagingIngestion.objects.create(source_kind="CSV", source_reference="independent")
    independent = staging.stage_source_batch(ingestion_id=other.pk, batch=batch)
    assert len({first.pk, second.pk, independent.pk}) == 3
    assert first.content_hash == second.content_hash == independent.content_hash
    assert first.records.count() == second.records.count() == independent.records.count() == 3


def test_successful_staging_rolls_back_with_outer_transaction(ingestion, batch):
    record_count_before = StagingRecord.objects.count()
    with pytest.raises(RuntimeError, match="synthetic outer transaction failure"):
        with transaction.atomic():
            staged = staging.stage_source_batch(ingestion_id=ingestion.pk, batch=batch)
            record_ids = list(staged.records.values_list("pk", flat=True))
            assert StagingBatch.objects.filter(pk=staged.pk).exists()
            assert len(record_ids) == staged.row_count == len(batch)
            raise RuntimeError("synthetic outer transaction failure")

    assert not StagingBatch.objects.filter(ingestion=ingestion).exists()
    assert not StagingRecord.objects.filter(batch_id=staged.pk).exists()
    assert not StagingRecord.objects.filter(pk__in=record_ids).exists()
    assert StagingRecord.objects.count() == record_count_before
    assert StagingIngestion.objects.filter(pk=ingestion.pk).exists()


def test_equal_content_under_next_batch_number_has_distinct_identity(ingestion, batch):
    """Retries reuse the original key; a matching hash must not deduplicate batches."""
    first = staging.stage_source_batch(ingestion_id=ingestion.pk, batch=batch)
    next_batch = replace(batch, batch_number=1)
    second = staging.stage_source_batch(ingestion_id=ingestion.pk, batch=next_batch)

    assert first.pk != second.pk
    assert first.ingestion_id == second.ingestion_id == ingestion.pk
    assert (first.batch_number, second.batch_number) == (0, 1)
    assert first.content_hash == second.content_hash == fingerprint_source_batch(batch)
    first_record_ids = set(first.records.values_list("pk", flat=True))
    second_record_ids = set(second.records.values_list("pk", flat=True))
    assert len(first_record_ids) == len(second_record_ids) == len(batch)
    assert first_record_ids.isdisjoint(second_record_ids)
    for persisted, source in ((first, batch), (second, next_batch)):
        assert _payload(persisted) == encode_source_batch(source)
        assert (
            staging.stage_source_batch(ingestion_id=ingestion.pk, batch=source).pk == persisted.pk
        )
    assert StagingBatch.objects.filter(ingestion=ingestion).count() == 2
    assert StagingRecord.objects.filter(batch__ingestion=ingestion).count() == 2 * len(batch)


def test_empty_batch_and_empty_source_name_are_supported(ingestion, batch):
    empty = replace(batch, data=batch.data.iloc[:0].copy(), source_name="")
    result = staging.stage_source_batch(ingestion_id=ingestion.pk, batch=empty)
    assert result.row_count == result.records.count() == 0
    assert _payload(result) == encode_source_batch(empty)
    assert staging.stage_source_batch(ingestion_id=ingestion.pk, batch=empty).pk == result.pk


def test_invalid_or_missing_identity_does_not_create_an_ingestion(ingestion, batch):
    count = StagingIngestion.objects.count()
    for identity in (None, str(ingestion.pk)):
        with pytest.raises(StagingIntegrityError):
            staging.stage_source_batch(ingestion_id=identity, batch=batch)
    for number in (-1, True, 0.0):
        with pytest.raises(StagingIntegrityError):
            staging.stage_source_batch(
                ingestion_id=ingestion.pk, batch=replace(batch, batch_number=number)
            )
    with pytest.raises(StagingIngestionError):
        staging.stage_source_batch(ingestion_id=uuid4(), batch=batch)
    assert StagingIngestion.objects.count() == count
    assert not StagingBatch.objects.filter(ingestion=ingestion).exists()


@pytest.mark.parametrize("damage", ["count", "position"])
def test_encoded_accounting_checked_before_writes(ingestion, batch, monkeypatch, damage):
    payload = encode_source_batch(batch)
    if damage == "count":
        payload["row_count"] += 1
    else:
        payload["records"][1]["row_position"] = 3
    monkeypatch.setattr(staging, "encode_source_batch", lambda _: payload)
    with pytest.raises(StagingIntegrityError):
        staging.stage_source_batch(ingestion_id=ingestion.pk, batch=batch)
    assert not StagingBatch.objects.filter(ingestion=ingestion).exists()


@pytest.mark.parametrize("failure", ["exception", "incomplete"])
def test_mid_insert_failure_rolls_back_header_and_all_records(
    ingestion, batch, monkeypatch, failure
):
    original_bulk_create = QuerySet.bulk_create
    calls = 0
    record_count_before = StagingRecord.objects.count()

    def interrupted_insert(query, objects, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            assert StagingRecord.objects.filter(batch__ingestion=ingestion).count() == 1
            if failure == "exception":
                raise RuntimeError("synthetic mid-insert failure")
            return []
        return original_bulk_create(query, objects, **kwargs)

    monkeypatch.setattr(staging, "_INSERT_BATCH_SIZE", 1)
    monkeypatch.setattr(QuerySet, "bulk_create", interrupted_insert)
    with pytest.raises(RuntimeError if failure == "exception" else StagingIntegrityError):
        staging.stage_source_batch(ingestion_id=ingestion.pk, batch=batch)
    assert not StagingBatch.objects.filter(ingestion=ingestion).exists()
    assert StagingRecord.objects.count() == record_count_before


@pytest.mark.parametrize("damage", ["missing", "position", "header", "payload", "hash", "storage"])
def test_corrupt_staging_is_never_accepted_as_retry(ingestion, batch, damage):
    committed = staging.stage_source_batch(ingestion_id=ingestion.pk, batch=batch)
    rows = committed.records.order_by("row_position")
    first = rows.first()
    if damage == "missing":
        first.delete()
    elif damage == "position":
        rows.filter(pk=first.pk).update(row_position=10)
    elif damage == "header":
        header = deepcopy(committed.header)
        header["source_name"] = "different synthetic metadata"
        StagingBatch.objects.filter(pk=committed.pk).update(header=header)
    elif damage == "payload":
        payload = deepcopy(first.record_payload)
        payload["values"][0] = {"type": "invalid-synthetic-tag"}
        rows.filter(pk=first.pk).update(record_payload=payload)
    elif damage == "hash":
        StagingBatch.objects.filter(pk=committed.pk).update(content_hash="0" * 64)
    else:
        with connections["default"].cursor() as cursor:
            cursor.execute(
                "UPDATE etl_stagingrecord SET record_payload = %s WHERE id = %s",
                ['{"values":[],"values":[1]}', first.pk],
            )
    with pytest.raises(StagingIntegrityError):
        staging.stage_source_batch(ingestion_id=ingestion.pk, batch=batch)
    assert StagingBatch.objects.filter(ingestion=ingestion).count() == 1
    assert committed.records.count() == (2 if damage == "missing" else 3)


def test_existing_sequence_gap_blocks_further_batches(ingestion, batch):
    first = staging.stage_source_batch(ingestion_id=ingestion.pk, batch=batch)
    header = {**first.header, "batch_number": 2}
    StagingBatch.objects.filter(pk=first.pk).update(batch_number=2, header=header)
    with pytest.raises(StagingIntegrityError, match="existing staging batch sequence"):
        staging.stage_source_batch(ingestion_id=ingestion.pk, batch=replace(batch, batch_number=3))
    assert StagingBatch.objects.filter(ingestion=ingestion).count() == 1


@pytest.mark.parametrize("conflicting", [False, True])
def test_concurrent_submissions_wait_for_parent_and_commit_once(ingestion, batch, conflicting):
    barrier = Barrier(3)
    backend_ids = Queue()
    changed = batch.data.copy(deep=True)
    changed.iloc[0, 2] = "different concurrent content"
    submissions = [batch, replace(batch, data=changed) if conflicting else batch]

    def submit(source):
        connection = connections["default"]
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '15s'")
                cursor.execute("SELECT pg_backend_pid()")
                backend_ids.put(cursor.fetchone()[0])
            barrier.wait(timeout=10)
            try:
                return staging.stage_source_batch(ingestion_id=ingestion.pk, batch=source).pk
            except StagingConflictError:
                return "conflict"
        finally:
            connection.close()

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        with transaction.atomic():
            StagingIngestion.objects.select_for_update().get(pk=ingestion.pk)
            futures = [executor.submit(submit, source) for source in submissions]
            barrier.wait(timeout=10)
            pids = [backend_ids.get(timeout=5), backend_ids.get(timeout=5)]
            deadline = time.monotonic() + 10
            waiting = 0
            while waiting < 2 and time.monotonic() < deadline:
                with connections["default"].cursor() as cursor:
                    cursor.execute(
                        "SELECT count(DISTINCT pid) FROM pg_locks "
                        "WHERE pid = ANY(%s) AND NOT granted",
                        [pids],
                    )
                    waiting = cursor.fetchone()[0]
                if waiting < 2:
                    time.sleep(0.01)
            assert (
                waiting == 2
            ), "Both independent PostgreSQL connections must wait on the parent lock"
            assert not StagingBatch.objects.filter(ingestion=ingestion).exists()
        results = [future.result(timeout=20) for future in futures]
    finally:
        executor.shutdown(wait=True)
    committed = StagingBatch.objects.get(ingestion=ingestion)
    assert results.count("conflict") == int(conflicting)
    assert all(value in (committed.pk, "conflict") for value in results)
    assert committed.records.count() == 3
    assert committed.content_hash == fingerprint_source_batch(
        decode_source_batch(_payload(committed))
    )
