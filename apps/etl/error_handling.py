"""Pure, conservative ETL failure descriptions (Volume 4.6, Gate 1).

Only classify_etl_failure() results are safe diagnostic projections. The original
exception may contain source values, credentials, SQL or paths; it is neither
stored in the result nor formatted. Existing exceptions and service behavior are
unchanged. No operation/stage, claim state or commit outcome is inferred.

Recovery guidance is not authorization to retry. Even a potentially recoverable
database interruption requires reconciliation using the original identities.
Terminal FAILED attempts remain terminal, including within bound ingestions.
"""

from dataclasses import dataclass
from enum import StrEnum

import psycopg
from django.db import DatabaseError, InterfaceError
from sqlalchemy.exc import DBAPIError

from etl.exceptions import (
    AmbiguousColumnsError,
    AttemptConflictError,
    AttemptIntegrityError,
    AttemptRegistrationError,
    ETLError,
    MalformedSourceError,
    MissingColumnsError,
    SerializationError,
    SourceConnectionError,
    SourceNotFoundError,
    StagingBatchNotFoundError,
    StagingConflictError,
    StagingIngestionError,
    StagingIntegrityError,
    TypeConversionError,
)
from etl.source_snapshots import SnapshotError


class FailureCategory(StrEnum):
    INPUT = "input"
    CONFIGURATION = "configuration"
    SOURCE_IO = "source_io"
    DATABASE = "database"
    INTEGRITY = "integrity"
    CONFLICT = "conflict"
    INTERRUPTED = "interrupted"
    UNCLASSIFIED = "unclassified"


class RecoveryGuidance(StrEnum):
    NO_UNCHANGED_RETRY = "no_unchanged_retry"
    OPERATOR_REVIEW = "operator_review"
    RECONCILE_FIRST = "reconcile_first"


class FailureReason(StrEnum):
    UNSPECIFIED = "unspecified"
    CONNECTION_INTERRUPTED = "connection_interrupted"
    TRANSACTION_CONFLICT = "transaction_conflict"
    DATABASE_CONFIGURATION = "database_configuration"


@dataclass(frozen=True, slots=True)
class ETLFailure:
    """Classifier-produced values contain only catalog text, never exception data."""

    code: str
    category: FailureCategory
    message: str
    recovery: RecoveryGuidance
    reason: FailureReason = FailureReason.UNSPECIFIED

    def as_dict(self) -> dict[str, str]:
        """Return an independent, JSON-compatible safe projection."""
        return {
            "code": self.code,
            "category": self.category.value,
            "message": self.message,
            "recovery": self.recovery.value,
            "reason": self.reason.value,
        }


# Capture trusted class defaults, never instance attributes or subclass overrides.
# The broad base class must remain last; unknown subclasses use its safe fallback.
_DOMAIN_FAILURES = (
    *(
        (
            error,
            ETLFailure(
                error.code,
                FailureCategory.INPUT,
                error.default_message,
                RecoveryGuidance.NO_UNCHANGED_RETRY,
            ),
        )
        for error in (
            MalformedSourceError,
            MissingColumnsError,
            AmbiguousColumnsError,
            TypeConversionError,
        )
    ),
    *(
        (
            error,
            ETLFailure(
                error.code,
                FailureCategory.INTEGRITY,
                error.default_message,
                RecoveryGuidance.OPERATOR_REVIEW,
            ),
        )
        for error in (SerializationError, StagingIntegrityError, AttemptIntegrityError)
    ),
    *(
        (
            error,
            ETLFailure(
                error.code,
                FailureCategory.CONFLICT,
                error.default_message,
                RecoveryGuidance.OPERATOR_REVIEW,
            ),
        )
        for error in (StagingConflictError, AttemptConflictError)
    ),
    *(
        (
            error,
            ETLFailure(
                error.code,
                FailureCategory.CONFIGURATION,
                error.default_message,
                RecoveryGuidance.OPERATOR_REVIEW,
            ),
        )
        for error in (SourceNotFoundError, StagingBatchNotFoundError)
    ),
    *(
        (
            error,
            ETLFailure(
                error.code,
                FailureCategory.UNCLASSIFIED,
                message,
                RecoveryGuidance.OPERATOR_REVIEW,
            ),
        )
        for error, message in (
            (StagingIngestionError, StagingIngestionError.default_message),
            (AttemptRegistrationError, AttemptRegistrationError.default_message),
            (
                SnapshotError,
                "A snapshot operation failed. Inspect custody, availability and integrity.",
            ),
            (ETLError, ETLError.default_message),
        )
    ),
)

_DATABASE_ERRORS = (DatabaseError, InterfaceError, DBAPIError, psycopg.Error)
_CONNECTION_STATES = frozenset({"08003", "08006", "08007", "57P01", "57P02", "57P03"})
_TRANSACTION_STATES = frozenset({"40001", "40P01"})
_CONFIGURATION_STATES = frozenset({"28000", "28P01", "42501", "42703", "42P01"})


def _database_reason(exc):
    # Follow only the wrappers used by the accepted sources/application database.
    # Never inspect implicit __context__, arbitrary attributes, driver diagnostics
    # or exception text. Bound traversal also handles cyclic/malformed wrappers.
    for _ in range(4):
        if isinstance(exc, psycopg.Error):
            state = exc.sqlstate
            if type(state) is str and len(state) == 5:
                if state in _CONNECTION_STATES:
                    return FailureReason.CONNECTION_INTERRUPTED
                if state in _TRANSACTION_STATES:
                    return FailureReason.TRANSACTION_CONFLICT
                if state in _CONFIGURATION_STATES:
                    return FailureReason.DATABASE_CONFIGURATION
            break
        if isinstance(exc, DBAPIError):
            exc = exc.orig
        elif isinstance(exc, (DatabaseError, InterfaceError)):
            exc = exc.__cause__
        else:
            break
    return FailureReason.UNSPECIFIED


def _database_failure(exc, code):
    reason = _database_reason(exc)
    if reason == FailureReason.DATABASE_CONFIGURATION:
        return ETLFailure(
            code,
            FailureCategory.CONFIGURATION,
            "A database authentication, permission or schema check failed.",
            RecoveryGuidance.OPERATOR_REVIEW,
            reason,
        )
    if reason in (FailureReason.CONNECTION_INTERRUPTED, FailureReason.TRANSACTION_CONFLICT):
        return ETLFailure(
            code,
            FailureCategory.DATABASE,
            "A database operation was interrupted. Reconcile persisted state before any retry.",
            RecoveryGuidance.RECONCILE_FIRST,
            reason,
        )
    return ETLFailure(
        code,
        FailureCategory.DATABASE,
        "A database operation failed. Inspect configuration, availability and persisted state.",
        RecoveryGuidance.OPERATOR_REVIEW,
    )


def classify_etl_failure(exc: BaseException) -> ETLFailure:
    """Describe a failure without I/O, logging, mutation or recovery operations.

    Known domain codes are retained. Non-domain/unknown exceptions use etl_error.
    SQLSTATE allowlisting refines known database failures only; absent or unknown
    codes never imply transience. Ambiguous snapshot, registration and claim
    failures require operator inspection, not message-based guesses.
    """
    if not isinstance(exc, BaseException):
        raise TypeError("Expected an exception instance.")
    if isinstance(exc, SourceConnectionError):
        return _database_failure(exc.__cause__, SourceConnectionError.code)
    for error_type, failure in _DOMAIN_FAILURES:
        if isinstance(exc, error_type):
            return failure
    if isinstance(exc, _DATABASE_ERRORS):
        return _database_failure(exc, ETLError.code)
    if isinstance(exc, (InterruptedError, KeyboardInterrupt)):
        return ETLFailure(
            ETLError.code,
            FailureCategory.INTERRUPTED,
            "ETL processing was interrupted. Reconcile persisted state before any retry.",
            RecoveryGuidance.RECONCILE_FIRST,
        )
    if isinstance(exc, OSError):
        return ETLFailure(
            ETLError.code,
            FailureCategory.SOURCE_IO,
            "An I/O operation failed. Inspect resource availability and permissions.",
            RecoveryGuidance.OPERATOR_REVIEW,
        )
    return _DOMAIN_FAILURES[-1][1]
