"""Read one immutable staged SER-v1 batch from the application PostgreSQL database.

The accepted writer commits headers and records atomically and does not modify
committed batches. Ordinary SELECTs therefore need no row locks or additional
snapshot transaction. Out-of-band concurrent edits are outside that contract;
integrity checks reject inconsistent content, not authenticate its history.

Fetching uses bounded iterator chunks, but reconstruction materializes one batch
in memory. This is neither constant-memory nor entire-ingestion streaming.
"""

from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import DEFAULT_DB_ALIAS, connections

from etl.exceptions import SerializationError, StagingBatchNotFoundError, StagingIntegrityError
from etl.models import StagingBatch, StagingRecord
from etl.serialization import decode_source_batch, fingerprint_source_batch
from etl.sources.contract import SourceBatch

_FETCH_CHUNK_SIZE = 1000


def _require(condition, message):
    if not condition:
        raise StagingIntegrityError(message)


def _reconstruct(staged):
    header = staged.header
    _require(
        type(header) is dict
        and "records" not in header
        and header.get("source_name") == staged.source_name
        and header.get("batch_number") == staged.batch_number
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
        .iterator(chunk_size=_FETCH_CHUNK_SIZE)
    )
    for position, (stored_position, record) in enumerate(rows):
        _require(
            stored_position == position
            and type(record) is dict
            and type(record.get("row_position")) is int
            and record["row_position"] == position,
            "The persisted staging row positions are incomplete or inconsistent.",
        )
        records.append(record)
    _require(
        len(records) == staged.row_count,
        "The persisted staging row count is inconsistent.",
    )
    reconstructed = decode_source_batch({**header, "records": records})
    _require(
        fingerprint_source_batch(reconstructed) == staged.content_hash,
        "The reconstructed staging fingerprint disagrees with its stored hash.",
    )
    return reconstructed


def load_source_batch(*, ingestion_id: UUID, batch_number: int) -> SourceBatch:
    """Return a fresh, verified SourceBatch for exactly the requested logical key.

    OPEN, COMPLETE and ABORTED ingestions are readable. No INSERT, UPDATE, DELETE,
    repair or transformation is performed. Missing batches raise
    StagingBatchNotFoundError; invalid identity or persisted content raises
    StagingIntegrityError. Unexpected database/programming errors propagate.

    The original extracted_at is decoded from the stored header. SER-v1's content
    fingerprint excludes extracted_at and batch_number: it cannot detect a valid
    timestamp-only edit. Batch number is checked against relational identity.
    """
    _require(type(ingestion_id) is UUID, "A UUID ingestion identity is required.")
    _require(
        type(batch_number) is int and batch_number >= 0,
        "A nonnegative integer batch number is required.",
    )
    _require(
        connections[DEFAULT_DB_ALIAS].vendor == "postgresql",
        "Staging reconstruction requires the application PostgreSQL database.",
    )
    try:
        staged = StagingBatch.objects.using(DEFAULT_DB_ALIAS).get(
            ingestion_id=ingestion_id, batch_number=batch_number
        )
        return _reconstruct(staged)
    except StagingBatch.DoesNotExist:
        raise StagingBatchNotFoundError() from None
    except (ValidationError, SerializationError):
        raise StagingIntegrityError("The stored staging representation is invalid.") from None
