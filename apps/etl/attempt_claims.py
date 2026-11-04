"""PostgreSQL leases for attempt ownership, not scheduling or exactly-once execution.

TTL is 1..3600 seconds. clock_timestamp() is read only after taking the attempt
lock, so waiting and long outer transactions cannot make a stale clock authoritative.
An outer transaction still controls durability. Legacy unclaimed RUNNING work is
never taken over automatically. Only cooperating service writers are fenced;
arbitrary SQL writers are outside this contract. Tokens must not be logged.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import uuid4

from django.db import DEFAULT_DB_ALIAS, connections, transaction

from etl.attempt_config import _stored_profile
from etl.exceptions import AttemptConflictError, AttemptIntegrityError, AttemptRegistrationError
from etl.models import TransformationAttempt, TransformationRecordResult

_MAX_GENERATION = 2**63 - 1


@dataclass(frozen=True)
class ClaimToken:
    """Internal ownership handle, not user authentication; owner omitted from repr."""

    attempt_id: int
    owner_token: str = field(repr=False)
    generation: int


def _positive_integer(value):
    return type(value) is int and 1 <= value <= _MAX_GENERATION


def _ttl(value):
    if type(value) is not int or not 1 <= value <= 3600:
        raise AttemptRegistrationError("Claim TTL must be an integer from 1 to 3600 seconds.")
    return timedelta(seconds=value)


def _validate_token(claim):
    if (
        type(claim) is not ClaimToken
        or not _positive_integer(claim.attempt_id)
        or not _positive_integer(claim.generation)
        or type(claim.owner_token) is not str
        or len(claim.owner_token) != 32
        or any(c not in "0123456789abcdef" for c in claim.owner_token)
    ):
        raise AttemptRegistrationError("A valid claim token is required.")


def _locked(attempt_id):
    if connections[DEFAULT_DB_ALIAS].vendor != "postgresql":
        raise AttemptRegistrationError("Attempt claims require application PostgreSQL.")
    if not _positive_integer(attempt_id):
        raise AttemptRegistrationError("A positive integer attempt ID is required.")
    try:
        attempt = (
            TransformationAttempt.objects.using(DEFAULT_DB_ALIAS)
            .select_for_update()
            .get(pk=attempt_id)
        )
    except TransformationAttempt.DoesNotExist:
        raise AttemptRegistrationError("The requested attempt does not exist.") from None
    # Use the locked instance: reject corruption before any claim/lifecycle write.
    _stored_profile(attempt)
    return attempt


def _database_now() -> datetime:
    """Caller must hold the attempt row lock in its application DB transaction."""
    with connections[DEFAULT_DB_ALIAS].cursor() as cursor:
        cursor.execute("SELECT clock_timestamp()")
        return cursor.fetchone()[0]


def _validate_claim_state(attempt):
    """Check nonterminal ownership shape; never downgrade a managed generation."""
    generation = attempt.claim_generation
    empty = attempt.claim_owner is None and attempt.claim_expires_at is None
    if type(generation) is not int or not 0 <= generation <= _MAX_GENERATION:
        raise AttemptIntegrityError("Invalid stored claim generation.")
    if generation == 0 or attempt.status == "REGISTERED":
        if generation != 0 or not empty:
            raise AttemptIntegrityError("Unexpected claim metadata on an unclaimed attempt.")
    elif (
        type(attempt.claim_owner) is not str
        or not attempt.claim_owner
        or attempt.claim_expires_at is None
    ):
        raise AttemptIntegrityError("Managed attempt has incomplete ownership metadata.")


def _verify_claim(attempt, claim: ClaimToken | None) -> datetime | None:
    """Fence a nonterminal write while its caller holds the row lock.

    Call again immediately before terminal persistence after expensive work.
    Verified read-only terminal retries deliberately do not call this helper.
    """
    _validate_claim_state(attempt)
    if attempt.claim_generation == 0:
        if claim is not None:
            raise AttemptConflictError("An unclaimed attempt cannot use a claim token.")
        return None
    if claim is None:
        raise AttemptConflictError("Managed attempt requires current ownership.")
    _validate_token(claim)
    if (
        attempt.status != "RUNNING"
        or claim.attempt_id != attempt.pk
        or claim.owner_token != attempt.claim_owner
        or claim.generation != attempt.claim_generation
    ):
        raise AttemptConflictError("Claim ownership has changed or does not match.")
    now = _database_now()
    if attempt.claim_expires_at <= now:
        raise AttemptConflictError("The claim has expired.")
    return now


def _no_results(attempt):
    if (
        TransformationRecordResult.objects.using(DEFAULT_DB_ALIAS)
        .filter(attempt_id=attempt.pk)
        .exists()
    ):
        raise AttemptIntegrityError("An unfinished attempt has persisted results.")


def claim_attempt(*, attempt_id: int, ttl_seconds: int) -> ClaimToken:
    """Atomically start virgin REGISTERED work or reclaim expired managed RUNNING work.

    Active ownership and legacy unclaimed RUNNING work cannot be stolen. Recovery
    preserves identity and started_at, advances generation once, and never marks
    an expired attempt FAILED. A lost acquisition response requires waiting for
    expiry/recovery; this operation is not token-less acquisition replay.
    """
    duration = _ttl(ttl_seconds)
    with transaction.atomic(using=DEFAULT_DB_ALIAS):
        attempt = _locked(attempt_id)
        if attempt.status not in ("REGISTERED", "RUNNING"):
            raise AttemptConflictError("A terminal attempt cannot be claimed.")
        _validate_claim_state(attempt)
        _no_results(attempt)
        now = _database_now()
        if attempt.status == "RUNNING":
            if attempt.claim_generation == 0:
                raise AttemptConflictError("Unclaimed running work is ineligible for recovery.")
            if attempt.claim_expires_at > now:
                raise AttemptConflictError("An active claim cannot be replaced.")
        if attempt.claim_generation == _MAX_GENERATION:
            raise AttemptIntegrityError("Claim generation cannot be advanced further.")
        if attempt.status == "REGISTERED":
            attempt.status = "RUNNING"
            attempt.started_at = now
        attempt.claim_generation += 1
        attempt.claim_owner = uuid4().hex
        attempt.claim_expires_at = now + duration
        attempt.save(
            using=DEFAULT_DB_ALIAS,
            update_fields=[
                "status",
                "started_at",
                "claim_generation",
                "claim_owner",
                "claim_expires_at",
                "updated_at",
            ],
        )
        return ClaimToken(attempt.pk, attempt.claim_owner, attempt.claim_generation)


def renew_claim(*, claim: ClaimToken, ttl_seconds: int) -> ClaimToken:
    """Extend live ownership without shortening expiry or changing owner/generation."""
    duration = _ttl(ttl_seconds)
    _validate_token(claim)
    with transaction.atomic(using=DEFAULT_DB_ALIAS):
        attempt = _locked(claim.attempt_id)
        if attempt.status != "RUNNING":
            raise AttemptConflictError("Only running ownership can be renewed.")
        _no_results(attempt)
        now = _verify_claim(attempt, claim)
        attempt.claim_expires_at = max(attempt.claim_expires_at, now + duration)
        attempt.save(using=DEFAULT_DB_ALIAS, update_fields=["claim_expires_at", "updated_at"])
        return claim
