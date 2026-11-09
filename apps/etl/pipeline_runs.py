"""Single-batch execution. TransformationAttempt owns the lifecycle and ledger.

Admission and claim acquisition commit before interpretation. Completion (including
its independent ledger read and final lease check) and the terminal run projection
share a transaction. A synchronous operation may outlast its lease. Recovery then
requires an explicit retry with the same identities. No batch completes an ingestion.
"""

from uuid import UUID

from django.db import DEFAULT_DB_ALIAS, connections, transaction
from django.db.models import Count, Max, Min

from etl.attempt_claims import _validate_claim_state, claim_attempt, renew_claim
from etl.exceptions import (
    AttemptConflictError,
    AttemptIntegrityError,
    AttemptRegistrationError,
    TypeConversionError,
)
from etl.models import PipelineRun, TransformationAttempt
from etl.sources.contract import SourceBatch
from etl.staging import stage_source_batch
from etl.staging_reader import load_source_batch
from etl.transformation_attempts import complete_attempt, fail_attempt, register_attempt
from etl.transforms import AliasMapper, DateNormalizer
from etl.transforms.date_contract import SourceDateProfile


def _check_entry(ttl):
    connection = connections[DEFAULT_DB_ALIAS]
    if connection.vendor != "postgresql":
        raise AttemptRegistrationError("Pipeline execution requires application PostgreSQL.")
    if connection.in_atomic_block or not connection.get_autocommit():
        raise AttemptRegistrationError("Pipeline execution requires outermost autocommit.")
    if type(ttl) is not int or not 1 <= ttl <= 3600:
        raise AttemptRegistrationError("Claim TTL must be an integer from 1 to 3600 seconds.")


def _check_batch_count(batch):
    # Unique (batch, position) plus these bounds proves exact zero-based coverage.
    count = batch.row_count
    if not 0 <= count <= 2**31 - 1:
        raise AttemptIntegrityError("Staged row count exceeds the PipelineRun counter range.")
    positions = batch.records.using(DEFAULT_DB_ALIAS).aggregate(
        n=Count("pk"), first=Min("row_position"), last=Max("row_position")
    )
    if positions != {
        "n": count,
        "first": 0 if count else None,
        "last": count - 1 if count else None,
    }:
        raise AttemptIntegrityError("Staged positions disagree with the declared row count.")


def _locked(attempt_id):
    return (
        TransformationAttempt.objects.using(DEFAULT_DB_ALIAS).select_for_update().get(pk=attempt_id)
    )


def _project(attempt):
    """Called under the attempt lock, and only after verification for terminal states."""
    state = PipelineRun.Status
    target = state.PENDING if attempt.status == "REGISTERED" else state(attempt.status.lower())
    runs = PipelineRun.objects.using(DEFAULT_DB_ALIAS)
    run = runs.select_for_update().filter(attempt_id=attempt.pk).first()
    count = attempt.batch.row_count
    if target == state.SUCCEEDED and attempt.safe_row_count + attempt.unresolved_row_count != count:
        raise AttemptIntegrityError("Terminal attempt counts disagree with the staged batch.")
    if target in (state.FAILED, state.SUCCEEDED) and (
        attempt.claim_owner is not None or attempt.claim_expires_at is not None
    ):
        raise AttemptIntegrityError("Terminal attempt retains claim ownership.")
    times = (attempt.started_at, attempt.finished_at)
    if run is None:
        return runs.create(
            attempt_id=attempt.pk,
            status=target,
            started_at=times[0],
            finished_at=times[1],
            records_ingested=count,
            triggered_by_id=attempt.batch.ingestion.initiated_by_id,
        )
    # Managed attribution follows the ingestion. Deleting the user clears both FKs.
    if run.triggered_by_id != attempt.batch.ingestion.initiated_by_id:
        raise AttemptIntegrityError("Managed run initiator conflicts with its ingestion.")
    if (
        run.records_ingested,
        run.records_healed,
        run.records_quarantined,
        run.fault_counts,
        run.cdqi_scores,
    ) != (count, 0, 0, {}, {}):
        raise AttemptIntegrityError("Managed run accounting conflicts with its attempt.")
    if run.status == target and (run.started_at, run.finished_at) == times:
        return run
    if run.status == state.PENDING:
        compatible = run.started_at is None and run.finished_at is None and target != state.PENDING
    elif run.status == state.RUNNING:
        compatible = (
            target in (state.SUCCEEDED, state.FAILED)
            and run.started_at is not None
            and run.started_at == attempt.started_at
            and run.finished_at is None
        )
    else:
        compatible = False
    if not compatible:
        raise AttemptIntegrityError("Managed run lifecycle conflicts with its attempt.")
    run.status = target
    run.started_at, run.finished_at = times
    run.save(
        using=DEFAULT_DB_ALIAS, update_fields=["status", "started_at", "finished_at", "updated_at"]
    )
    return run


def stage_and_run_batch(
    *,
    ingestion_id: UUID,
    batch: SourceBatch,
    attempt_key: UUID,
    profile: SourceDateProfile,
    transformation_version: str,
    claim_ttl_seconds: int,
) -> PipelineRun:
    """Commit or verify the raw batch, then execute against its persisted original."""
    _check_entry(claim_ttl_seconds)
    staged = stage_source_batch(ingestion_id=ingestion_id, batch=batch)
    return run_staged_batch(
        ingestion_id=ingestion_id,
        batch_number=staged.batch_number,
        attempt_key=attempt_key,
        profile=profile,
        transformation_version=transformation_version,
        claim_ttl_seconds=claim_ttl_seconds,
    )


def run_staged_batch(
    *,
    ingestion_id: UUID,
    batch_number: int,
    attempt_key: UUID,
    profile: SourceDateProfile,
    transformation_version: str,
    claim_ttl_seconds: int,
) -> PipelineRun:
    """Execute or reconcile one attempt. Controlled conversion failure returns a FAILED run."""
    _check_entry(claim_ttl_seconds)
    with transaction.atomic(using=DEFAULT_DB_ALIAS):
        attempt = register_attempt(
            ingestion_id=ingestion_id,
            batch_number=batch_number,
            attempt_key=attempt_key,
            profile=profile,
            transformation_version=transformation_version,
        )
        # Registration inserted this row or locked the existing identity.
        _check_batch_count(attempt.batch)
        if attempt.status in ("REGISTERED", "RUNNING"):
            _validate_claim_state(attempt)
            if attempt.results.using(DEFAULT_DB_ALIAS).exists():
                raise AttemptIntegrityError("Unfinished attempt has committed outcomes.")
            _project(attempt)

    claim = None
    with transaction.atomic(using=DEFAULT_DB_ALIAS):
        attempt = _locked(attempt.pk)
        if attempt.status == "FAILED":
            verified = fail_attempt(
                attempt_id=attempt.pk,
                failure_code=attempt.failure_code,
                failure_stage=attempt.failure_stage,
                diagnostics=attempt.failure_diagnostics,
            )
            return _project(verified)
        if attempt.status != "SUCCEEDED":
            claim = claim_attempt(attempt_id=attempt.pk, ttl_seconds=claim_ttl_seconds)
            attempt.refresh_from_db(using=DEFAULT_DB_ALIAS)
            _project(attempt)

    source = load_source_batch(ingestion_id=ingestion_id, batch_number=batch_number)
    if claim is not None:
        renew_claim(claim=claim, ttl_seconds=claim_ttl_seconds)
    mapped = AliasMapper().transform(source)
    try:
        result = DateNormalizer(profile).apply(mapped)
    except TypeConversionError:
        if claim is None:
            raise AttemptIntegrityError(
                "Successful attempt interpretation cannot be reproduced."
            ) from None
        result = None

    if claim is not None:
        renew_claim(claim=claim, ttl_seconds=claim_ttl_seconds)
    with transaction.atomic(using=DEFAULT_DB_ALIAS):
        attempt = _locked(attempt.pk)
        if claim is not None and attempt.status != "RUNNING":
            raise AttemptConflictError("Attempt ownership ended before finalization.")
        if result is None:
            attempt = fail_attempt(
                attempt_id=attempt.pk,
                failure_code="TYPE_CONVERSION_ERROR",
                failure_stage="INTERPRETATION",
                diagnostics={"input_rows": len(source)},
                claim=claim,
            )
        else:
            attempt = complete_attempt(
                attempt_id=attempt.pk,
                result=result,
                transformation_version=transformation_version,
                claim=claim,
            )
        return _project(attempt)
