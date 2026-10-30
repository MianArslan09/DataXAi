"""Atomic raw staging on the application PostgreSQL connection (Volume 4.4.3).

All writers using this service serialize on the parent ingestion. Stored batches
are immutable here: a retry verifies their contents and retains original extraction
provenance. Atomicity does not imply an independent durable commit: inside an outer
transaction, a successful return only releases the service's savepoint. Durability
depends on the outermost commit; an outer rollback also removes the staged batch.

PostgresSource currently has no deterministic ORDER BY. Re-extraction can change
batch grouping or row order; an existing identity then conflicts rather than being
silently reused. Retry safety does not guarantee deterministic re-extraction across
sessions. This service does not schedule retries or complete ingestions.
"""

from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import DEFAULT_DB_ALIAS, connections, transaction
from django.db.models import Count, Max, Min

from etl.exceptions import (
    SerializationError,
    StagingConflictError,
    StagingIngestionError,
    StagingIntegrityError,
)
from etl.models import StagingBatch, StagingIngestion, StagingRecord
from etl.serialization import decode_source_batch, encode_source_batch, fingerprint_source_batch
from etl.sources.contract import SourceBatch

_INSERT_BATCH_SIZE = 1000


def _require(condition, message):
    if not condition:
        raise StagingIntegrityError(message)


def _check_rows(payload):
    count = payload.get("row_count")
    records = payload.get("records")
    _require(
        type(count) is int and count >= 0 and type(records) is list and len(records) == count,
        "The encoded staging row count is inconsistent.",
    )
    for position, record in enumerate(records):
        _require(
            type(record) is dict
            and type(record.get("row_position")) is int
            and record["row_position"] == position,
            "Staging row positions must be contiguous from zero.",
        )


def _verify_existing(staged):
    """Check both relational identity and authoritative SER-v1 content on replay."""
    header = staged.header
    _require(
        "records" not in header
        and header.get("batch_number") == staged.batch_number
        and header.get("source_name") == staged.source_name
        and header.get("row_count") == staged.row_count
        and header.get("version") == staged.serializer_version,
        "The stored staging header disagrees with its batch metadata.",
    )
    records = []
    rows = (
        StagingRecord.objects.using(DEFAULT_DB_ALIAS)
        .filter(batch_id=staged.pk)
        .order_by("row_position")
        .values_list("row_position", "record_payload")
        .iterator(chunk_size=_INSERT_BATCH_SIZE)
    )
    for position, (stored_position, record) in enumerate(rows):
        _require(
            stored_position == position,
            "The persisted staging row positions are incomplete or inconsistent.",
        )
        records.append(record)
    payload = {**header, "records": records}
    _check_rows(payload)
    reconstructed = decode_source_batch(payload)
    _require(
        fingerprint_source_batch(reconstructed) == staged.content_hash,
        "The reconstructed staging fingerprint disagrees with its stored hash.",
    )


def stage_source_batch(*, ingestion_id: UUID, batch: SourceBatch) -> StagingBatch:
    """Persist one complete batch or verify/reuse its existing logical identity.

    Identity is (ingestion_id, batch_number). New batches require OPEN and the next
    contiguous number; verified identical retries also work after COMPLETE/ABORTED.
    Callers must reuse the original pair for retries. Fingerprints verify content;
    equal content under another valid sequential batch number is a distinct batch,
    never deduplicated by its hash.
    Extraction timestamps are not content identity and are never overwritten.

    SER-v1 validates source values before bulk writes; row count/position checks
    are explicit because bulk_create() does not run model full_clean(). The accepted
    escaped-text fields handle database encoding. No source values enter errors.
    """
    _require(type(ingestion_id) is UUID, "A UUID ingestion identity is required.")
    _require(type(batch) is SourceBatch, "Raw staging requires a SourceBatch.")
    _require(
        type(batch.batch_number) is int and batch.batch_number >= 0,
        "A nonnegative integer batch number is required.",
    )
    _require(
        connections[DEFAULT_DB_ALIAS].vendor == "postgresql",
        "Raw staging requires the application PostgreSQL database.",
    )

    payload = encode_source_batch(batch)
    _check_rows(payload)
    # Hash the encoded snapshot, not a second read of the caller's mutable frame.
    content_hash = fingerprint_source_batch(decode_source_batch(payload))
    batch_number = payload["batch_number"]
    header = {key: value for key, value in payload.items() if key != "records"}

    with transaction.atomic(using=DEFAULT_DB_ALIAS):
        try:
            ingestion = (
                StagingIngestion.objects.using(DEFAULT_DB_ALIAS)
                .select_for_update()
                .get(pk=ingestion_id)
            )
        except StagingIngestion.DoesNotExist:
            raise StagingIngestionError("The requested staging ingestion does not exist.") from None

        batches = StagingBatch.objects.using(DEFAULT_DB_ALIAS).filter(ingestion_id=ingestion_id)
        try:
            existing = batches.filter(batch_number=batch_number).first()
            if existing is not None:
                _verify_existing(existing)
        except (ValidationError, SerializationError):
            raise StagingIntegrityError("The stored staging representation is invalid.") from None
        if existing is not None:
            if existing.content_hash != content_hash:
                raise StagingConflictError()
            return existing

        if ingestion.status != StagingIngestion.Status.OPEN:
            raise StagingIngestionError("Only an OPEN ingestion can accept a new batch.")
        sequence = batches.aggregate(
            count=Count("pk"), first=Min("batch_number"), last=Max("batch_number")
        )
        count = sequence["count"]
        _require(
            count == 0 or (sequence["first"] == 0 and sequence["last"] == count - 1),
            "The existing staging batch sequence is incomplete.",
        )
        _require(batch_number == count, "New staging batches must follow the sequence from zero.")

        staged = StagingBatch.objects.using(DEFAULT_DB_ALIAS).create(
            ingestion_id=ingestion_id,
            batch_number=batch_number,
            source_name=payload["source_name"],
            serializer_version=payload["version"],
            header=header,
            row_count=payload["row_count"],
            content_hash=content_hash,
        )
        for start in range(0, staged.row_count, _INSERT_BATCH_SIZE):
            StagingRecord.objects.using(DEFAULT_DB_ALIAS).bulk_create(
                [
                    StagingRecord(
                        batch_id=staged.pk,
                        row_position=record["row_position"],
                        record_payload=record,
                    )
                    for record in payload["records"][start : start + _INSERT_BATCH_SIZE]
                ],
                batch_size=_INSERT_BATCH_SIZE,
            )
        _require(
            StagingRecord.objects.using(DEFAULT_DB_ALIAS).filter(batch_id=staged.pk).count()
            == staged.row_count,
            "The persisted staging row count is inconsistent.",
        )
        return staged
