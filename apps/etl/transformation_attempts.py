"""Persist interpretation outcomes, not transformed records (Volume 4.4.5).

Configuration identity is SHA-256 of UTF-8 JSON, sorted keys, compact separators,
ensure_ascii=False and allow_nan=False. Logical identity remains (batch, attempt_key).
Only the selected attempt is locked; registration handles its named unique-key race
inside a savepoint. All operations use the application PostgreSQL connection.

Completion consumes an already computed result, never executes transformations.
It verifies represented provenance/accounting, not semantic correctness. Results
contain no ingestion UUID: indistinguishable source metadata cannot prove which
ingestion a caller originally used. Original IDs are always resolved from the
selected attempt. One batch is materialized; this is not constant-memory processing.
An enclosing transaction controls ultimate durability, including outer rollback.
"""

from uuid import UUID

import pandas as pd
from django.core.exceptions import ValidationError
from django.db import DEFAULT_DB_ALIAS, IntegrityError, connections, transaction
from django.utils import timezone

from etl.attempt_claims import ClaimToken, _validate_claim_state, _verify_claim
from etl.attempt_config import _CREDENTIAL as _CREDENTIAL
from etl.attempt_config import _IDENTIFIER as _IDENTIFIER
from etl.attempt_config import _configuration as _configuration
from etl.attempt_config import _hash as _hash
from etl.attempt_config import _identifier as _identifier
from etl.attempt_config import _require as _require
from etl.attempt_config import _stored_profile as _stored_profile
from etl.attempt_config import _text as _text
from etl.exceptions import (
    AttemptConflictError,
    AttemptIntegrityError,
    AttemptRegistrationError,
    ETLError,
    SerializationError,
    StagingBatchNotFoundError,
    StagingIntegrityError,
)
from etl.models import (
    StagingBatch,
    StagingRecord,
    TransformationAttempt,
    TransformationRecordResult,
    validate_failure_diagnostics,
)
from etl.serialization import _index as _ser_index
from etl.serialization import _scalar as _ser_scalar
from etl.staging_reader import load_source_batch
from etl.transforms.contract import TARGET_COLUMNS, TransformedBatch
from etl.transforms.date_contract import (
    DateEvidence,
    DateInterpretationResult,
    DateIssue,
    DateIssueCode,
    SourceDateProfile,
    SourceRow,
    TimezoneStatus,
)

_INSERT_BATCH_SIZE = 1000
_MAX_ID = 2**63 - 1


def _database():
    _require(
        connections[DEFAULT_DB_ALIAS].vendor == "postgresql",
        "Attempt persistence requires the application PostgreSQL database.",
        AttemptRegistrationError,
    )


def _integer(value, *, minimum=0):
    return type(value) is int and minimum <= value <= _MAX_ID


def _attempts():
    return TransformationAttempt.objects.using(DEFAULT_DB_ALIAS)


def _results(attempt):
    return TransformationRecordResult.objects.using(DEFAULT_DB_ALIAS).filter(attempt_id=attempt.pk)


def _locked(attempt_id):
    _require(
        _integer(attempt_id, minimum=1),
        "A positive attempt ID is required.",
        AttemptRegistrationError,
    )
    try:
        attempt = _attempts().select_for_update().get(pk=attempt_id)
    except TransformationAttempt.DoesNotExist:
        raise AttemptRegistrationError("The requested attempt does not exist.") from None
    _stored_profile(attempt)
    return attempt


def _no_results(attempt):
    _require(not _results(attempt).exists(), "An unfinished or failed attempt has stored results.")


def register_attempt(
    *,
    ingestion_id: UUID,
    batch_number: int,
    attempt_key: UUID,
    profile: SourceDateProfile,
    transformation_version: str,
) -> TransformationAttempt:
    """Register or verify a logical attempt, without running a transformation.

    Profile labels/provenance must be credential-free bounded text. Obvious secret
    assignments/URLs are rejected; callers must never supply opaque secrets as
    labels. Configuration uses the existing 16,384-character model bound.
    """
    _database()
    _require(
        type(ingestion_id) is UUID and type(attempt_key) is UUID and _integer(batch_number),
        "UUID identities and a nonnegative integer batch number are required.",
        AttemptRegistrationError,
    )
    config = _configuration(profile)
    version = _identifier(transformation_version, 128)
    try:
        batch = StagingBatch.objects.using(DEFAULT_DB_ALIAS).get(
            ingestion_id=ingestion_id, batch_number=batch_number
        )
    except StagingBatch.DoesNotExist:
        raise AttemptRegistrationError("The requested staged batch does not exist.") from None
    except ValidationError:
        raise AttemptIntegrityError("The stored staged batch metadata is invalid.") from None
    with transaction.atomic(using=DEFAULT_DB_ALIAS):
        query = _attempts().select_for_update().filter(batch_id=batch.pk, attempt_key=attempt_key)
        attempt = query.first()
        if attempt is None:
            try:
                with transaction.atomic(using=DEFAULT_DB_ALIAS):
                    return _attempts().create(
                        batch_id=batch.pk,
                        attempt_key=attempt_key,
                        configuration=config,
                        configuration_hash=_hash(config),
                        transformation_version=version,
                    )
            except IntegrityError as exc:
                if getattr(getattr(exc.__cause__, "diag", None), "constraint_name", None) != (
                    "stg_attempt_key_unique"
                ):
                    raise
                attempt = query.get()
        _stored_profile(attempt)
        _require(
            attempt.configuration == config and attempt.transformation_version == version,
            "The attempt key already has a different configuration or version.",
            AttemptConflictError,
        )
        return attempt


def start_attempt(*, attempt_id: int) -> TransformationAttempt:
    """Start unclaimed REGISTERED once; RUNNING retries never acquire ownership."""
    _database()
    with transaction.atomic(using=DEFAULT_DB_ALIAS):
        attempt = _locked(attempt_id)
        _require(
            attempt.status in ("REGISTERED", "RUNNING"),
            "A terminal attempt cannot be started.",
            AttemptConflictError,
        )
        _no_results(attempt)
        _validate_claim_state(attempt)
        if attempt.status == "REGISTERED":
            attempt.status = "RUNNING"
            attempt.started_at = timezone.now()
            attempt.save(
                using=DEFAULT_DB_ALIAS, update_fields=["status", "started_at", "updated_at"]
            )
        return attempt


def _same_index(actual, expected):
    # Reuse SER-v1's representation contract: pandas equality collapses Decimal
    # scale and signed zero, and can reject equivalent supported timezone objects.
    try:
        matches = _ser_index(actual) == _ser_index(expected)
    except SerializationError:
        raise AttemptIntegrityError("Interpretation index representation is invalid.") from None
    _require(matches, "Interpretation index lineage is inconsistent.")


def _row(row, position, source, index_label):
    _require(
        type(row) is SourceRow
        and type(row.source_name) is str
        and row.source_name == source.source_name
        and _integer(row.batch_number)
        and row.batch_number == source.batch_number
        and _integer(row.position)
        and row.position == position,
        "Interpretation source-row identity is inconsistent.",
    )
    try:
        matches = _ser_scalar(row.index_label, "index_label") == _ser_scalar(
            index_label, "index_label"
        )
    except SerializationError:
        raise AttemptIntegrityError("Interpretation index lineage is invalid.") from None
    _require(matches, "Interpretation index lineage is inconsistent.")


def _evidence(evidence, issue, profile):
    status, origin = evidence.timezone_status, evidence.timezone_origin
    _require(
        type(status) is TimezoneStatus
        and type(origin) is str
        and origin in {"explicit_offset", "source_profile", "unspecified"},
        "Invalid date interpretation status or origin.",
    )
    _require(
        (origin == "unspecified" and evidence.source_timezone is None)
        or (
            origin == "source_profile"
            and profile.timezone is not None
            and evidence.source_timezone == profile.timezone
        )
        or (
            origin == "explicit_offset"
            and type(evidence.source_timezone) is str
            and bool(evidence.source_timezone)
        ),
        "Date interpretation timezone provenance is inconsistent.",
    )
    local, utc = evidence.local_datetime, evidence.utc_datetime
    _require(
        local is None or (type(local) is pd.Timestamp and local.tzinfo is None),
        "Invalid local datetime evidence.",
    )
    if issue is not None:
        _require(
            status is TimezoneStatus.UNRESOLVED and utc is None,
            "Unresolved date evidence is inconsistent.",
        )
    elif status is TimezoneStatus.TIMEZONE_UNKNOWN:
        _require(
            origin == "unspecified"
            and profile.timezone is None
            and local is not None
            and utc is None,
            "Unknown-timezone evidence is inconsistent.",
        )
    else:
        _require(
            status is TimezoneStatus.UTC_NORMALIZED
            and origin != "unspecified"
            and local is not None
            and type(utc) is pd.Timestamp
            and str(utc.tzinfo) == "UTC",
            "Safe date evidence is inconsistent.",
        )
    return {
        "schema": "date-outcome-v1",
        "timezone_status": status.value,
        "timezone_origin": origin,
        "issue_code": None if issue is None else issue.code.value,
    }


def _ledger(attempt, result):
    _require(type(result) is DateInterpretationResult, "A DateInterpretationResult is required.")
    profile = _stored_profile(attempt)
    try:
        config = _configuration(result.profile)
    except AttemptRegistrationError:
        raise AttemptIntegrityError("Invalid interpretation profile.") from None
    _require(config == attempt.configuration, "Interpretation profile differs from the attempt.")
    for positions in (result.safe_positions, result.unresolved_positions):
        _require(
            type(positions) is tuple and all(_integer(p) for p in positions),
            "Invalid interpretation positions.",
        )
    _require(
        _integer(result.input_rows)
        and type(result.issues) is tuple
        and type(result.date_evidence) is tuple
        and all(type(e) is DateEvidence and type(e.row) is SourceRow for e in result.date_evidence)
        and all(type(i) is DateIssue and type(i.row) is SourceRow for i in result.issues),
        "Invalid interpretation structure.",
    )
    for split in (result.safe_batch, result.unresolved_batch):
        _require(
            type(split) is TransformedBatch and type(split.data) is pd.DataFrame,
            "Invalid interpretation batch.",
        )
        _require(
            all(list(split.data.columns).count(c) == 1 for c in TARGET_COLUMNS),
            "Interpretation schema is incomplete or ambiguous.",
        )
    try:
        result.check_accounting()
    except ETLError:
        raise AttemptIntegrityError(
            "Interpretation positional accounting is inconsistent."
        ) from None
    try:
        staged = StagingBatch.objects.using(DEFAULT_DB_ALIAS).get(pk=attempt.batch_id)
        source = load_source_batch(
            ingestion_id=staged.ingestion_id, batch_number=staged.batch_number
        )
    except (ValidationError, StagingIntegrityError, StagingBatchNotFoundError):
        raise AttemptIntegrityError("The original staged batch failed verification.") from None
    _require(
        result.input_rows == staged.row_count == len(source),
        "Interpretation input count disagrees with the staged batch.",
    )
    for split, positions in (
        (result.safe_batch, result.safe_positions),
        (result.unresolved_batch, result.unresolved_positions),
    ):
        _require(
            type(split.source_name) is str
            and split.source_name == source.source_name
            and _integer(split.batch_number)
            and split.batch_number == source.batch_number
            and type(split.extracted_at) is type(source.extracted_at)
            and split.extracted_at == source.extracted_at,
            "Interpretation batch metadata disagrees with the staged source.",
        )
        _same_index(split.data.index, source.data.iloc[list(positions)].index)
    # DateNormalizer obtains SourceRow labels by Series.items() index iteration.
    # For some numeric indexes that yields Python scalars while index[position]
    # yields NumPy scalars. Match that accepted convention without coercing labels.
    index_labels = list(source.data.index)
    issues = {}
    for issue, position in zip(result.issues, result.unresolved_positions, strict=True):
        _row(issue.row, position, source, index_labels[position])
        _require(
            type(issue.code) is DateIssueCode and issue.column == "invoice_date",
            "Invalid date issue classification.",
        )
        issues[position] = issue
    identities = list(
        StagingRecord.objects.using(DEFAULT_DB_ALIAS)
        .filter(batch_id=attempt.batch_id)
        .order_by("row_position")
        .values_list("row_position", "pk")
        .iterator(chunk_size=1000)
    )
    _require(
        [p for p, _ in identities] == list(range(result.input_rows)),
        "The staged source-record positions are incomplete.",
    )
    ledger = []
    for position, evidence in enumerate(result.date_evidence):
        _row(evidence.row, position, source, index_labels[position])
        issue = issues.get(position)
        ledger.append(
            (
                identities[position][1],
                "UNRESOLVED" if issue else "SAFE",
                _evidence(evidence, issue, profile),
            )
        )
    return ledger


def _verify_ledger(attempt, expected, *, retry=False):
    # JSONField reads SQL NULL and JSONB null as None; enforce SQL nullness in SQL.
    _require(
        not _results(attempt).filter(result_payload__isnull=False).exists(),
        "Persisted result payloads must be SQL NULL.",
    )
    actual = list(
        _results(attempt)
        .order_by("staged_record__row_position")
        .values_list(
            "batch_id", "staged_record_id", "outcome", "result_payload", "interpretation_evidence"
        )
        .iterator(chunk_size=1000)
    )
    _require(
        [row[1] for row in actual] == [row[0] for row in expected]
        and all(row[0] == attempt.batch_id and row[3] is None for row in actual),
        "The persisted ledger identities or payloads are inconsistent.",
    )
    for _, _, outcome, _, evidence in actual:
        _require(
            type(evidence) is dict
            and set(evidence) == {"schema", "timezone_status", "timezone_origin", "issue_code"}
            and evidence["schema"] == "date-outcome-v1"
            and type(evidence["timezone_status"]) is str
            and type(evidence["timezone_origin"]) is str
            and evidence["timezone_origin"] in {"unspecified", "source_profile", "explicit_offset"},
            "The stored interpretation evidence is invalid.",
        )
        status, origin, code = (
            evidence[k] for k in ("timezone_status", "timezone_origin", "issue_code")
        )
        _require(
            (
                outcome == "UNRESOLVED"
                and status == "UNRESOLVED"
                and type(code) is str
                and code in DateIssueCode._value2member_map_
            )
            or (
                outcome == "SAFE"
                and code is None
                and (
                    (status == "TIMEZONE_UNKNOWN" and origin == "unspecified")
                    or (status == "UTC_NORMALIZED" and origin != "unspecified")
                )
            ),
            "The stored outcome and evidence disagree.",
        )
    if retry:
        safe = sum(row[2] == "SAFE" for row in actual)
        _require(
            (attempt.safe_row_count, attempt.unresolved_row_count) == (safe, len(actual) - safe),
            "The successful attempt counts disagree with its stored ledger.",
        )
    _require(
        actual
        == [(attempt.batch_id, pk, outcome, None, evidence) for pk, outcome, evidence in expected],
        "The persisted interpretation ledger is incomplete or conflicting.",
        AttemptConflictError if retry else AttemptIntegrityError,
    )


def complete_attempt(
    *,
    attempt_id: int,
    result: DateInterpretationResult,
    transformation_version: str,
    claim: ClaimToken | None = None,
) -> TransformationAttempt:
    """Atomically persist a complete outcome ledger or verify a successful retry.

    Every result_payload stays SQL NULL. Evidence stores only schema, enum status,
    controlled origin and issue code; original values/reasons/datetimes are omitted.
    Caller must not concurrently mutate the supplied result during this call.
    Managed writes require a live claim before work and immediately before the
    terminal write. Verified terminal retries are read-only and need no claim.
    """
    _database()
    version = _identifier(transformation_version, 128)
    with transaction.atomic(using=DEFAULT_DB_ALIAS):
        attempt = _locked(attempt_id)
        _require(
            attempt.transformation_version == version,
            "The transformation version conflicts with the attempt.",
            AttemptConflictError,
        )
        _require(
            attempt.status in ("RUNNING", "SUCCEEDED"),
            "Only RUNNING attempts can complete; FAILED is terminal.",
            AttemptConflictError,
        )
        if attempt.status == "RUNNING":
            _verify_claim(attempt, claim)
        ledger = _ledger(attempt, result)
        safe = sum(outcome == "SAFE" for _, outcome, _ in ledger)
        unresolved = len(ledger) - safe
        if attempt.status == "SUCCEEDED":
            _verify_ledger(attempt, ledger, retry=True)
            return attempt
        _no_results(attempt)
        for start in range(0, len(ledger), _INSERT_BATCH_SIZE):
            TransformationRecordResult.objects.using(DEFAULT_DB_ALIAS).bulk_create(
                [
                    TransformationRecordResult(
                        batch_id=attempt.batch_id,
                        attempt_id=attempt.pk,
                        staged_record_id=pk,
                        outcome=outcome,
                        result_payload=None,
                        interpretation_evidence=evidence,
                    )
                    for pk, outcome, evidence in ledger[start : start + _INSERT_BATCH_SIZE]
                ],
                batch_size=_INSERT_BATCH_SIZE,
            )
        _verify_ledger(attempt, ledger)
        finished_at = _verify_claim(attempt, claim) or timezone.now()
        attempt.status = "SUCCEEDED"
        attempt.safe_row_count, attempt.unresolved_row_count = safe, unresolved
        attempt.finished_at = finished_at
        attempt.claim_owner = None
        attempt.claim_expires_at = None
        attempt.save(
            using=DEFAULT_DB_ALIAS,
            update_fields=[
                "status",
                "safe_row_count",
                "unresolved_row_count",
                "finished_at",
                "claim_owner",
                "claim_expires_at",
                "updated_at",
            ],
        )
        return attempt


def _diagnostics(value):
    if value is None:
        return None
    _require(
        type(value) is dict,
        "Diagnostics must be a controlled JSON object.",
        AttemptRegistrationError,
    )
    allowed = {"row_position", "column_position", "input_rows", "retryable"}
    _require(
        all(type(k) is str and k in allowed for k in value),
        "Unsupported diagnostic fields.",
        AttemptRegistrationError,
    )
    for key, item in value.items():
        _require(
            type(item) is bool if key == "retryable" else _integer(item),
            "Diagnostics accept only bounded positions/counts and a retryable Boolean.",
            AttemptRegistrationError,
        )
    try:
        validate_failure_diagnostics(value)
    except ValidationError:
        raise AttemptRegistrationError("Failure diagnostics exceed the storage bound.") from None
    return dict(value)


def fail_attempt(
    *,
    attempt_id: int,
    failure_code: str,
    failure_stage: str,
    diagnostics: dict | None = None,
    claim: ClaimToken | None = None,
) -> TransformationAttempt:
    """Record controlled failure without manufacturing outcomes.

    Codes/stages are ASCII identifiers <=64 characters. Optional diagnostics allow
    only nonnegative signed-bigint row_position/column_position/input_rows and a
    Boolean retryable flag. No exception, traceback or free-text values are accepted.
    Managed writes require current ownership; identical terminal retries are read-only.
    """
    _database()
    code, stage = _identifier(failure_code, 64), _identifier(failure_stage, 64)
    details = _diagnostics(diagnostics)
    with transaction.atomic(using=DEFAULT_DB_ALIAS):
        attempt = _locked(attempt_id)
        _require(
            attempt.status in ("REGISTERED", "RUNNING", "FAILED"),
            "A successful attempt cannot be failed.",
            AttemptConflictError,
        )
        if attempt.status != "FAILED":
            _verify_claim(attempt, claim)
        _no_results(attempt)
        if attempt.status == "FAILED":
            _require(
                (attempt.failure_code, attempt.failure_stage, attempt.failure_diagnostics)
                == (code, stage, details),
                "The recorded failure differs from this retry.",
                AttemptConflictError,
            )
            return attempt
        finished_at = _verify_claim(attempt, claim) or timezone.now()
        attempt.status = "FAILED"
        attempt.failure_code, attempt.failure_stage, attempt.failure_diagnostics = (
            code,
            stage,
            details,
        )
        attempt.finished_at = finished_at
        attempt.claim_owner = None
        attempt.claim_expires_at = None
        attempt.save(
            using=DEFAULT_DB_ALIAS,
            update_fields=[
                "status",
                "failure_code",
                "failure_stage",
                "failure_diagnostics",
                "finished_at",
                "claim_owner",
                "claim_expires_at",
                "updated_at",
            ],
        )
        return attempt
