"""
ETL-specific exceptions (M1, Volume 4). All subclass core.exceptions.DataXAiError
so they flow through the same envelope/handler established in Volume 3 -
no parallel exception-handling system.
"""

from core.exceptions import DataXAiError


class ETLError(DataXAiError):
    """Base class for every ETL-specific exception."""

    default_message = "An ETL error occurred."
    code = "etl_error"


class SourceNotFoundError(ETLError):
    default_message = "The requested source file or connection could not be found."
    code = "source_not_found"


class MalformedSourceError(ETLError):
    default_message = "The source data could not be parsed."
    code = "malformed_source"


class MissingColumnsError(ETLError):
    default_message = "The source data is missing required columns."
    code = "missing_columns"


class AmbiguousColumnsError(ETLError):
    default_message = "The source data contains duplicate required columns."
    code = "ambiguous_columns"


class SourceConnectionError(ETLError):
    default_message = "Could not connect to the source."
    code = "source_connection_error"


class SerializationError(ETLError):
    """Unsupported SourceBatch representation or malformed SER-v1 payload."""

    default_message = "The source batch could not be serialized or reconstructed faithfully."
    code = "serialization_error"


class StagingIngestionError(ETLError):
    """The requested ingestion is absent or cannot accept a new batch."""

    default_message = "The ingestion cannot accept the requested staging operation."
    code = "staging_ingestion_error"


class StagingConflictError(ETLError):
    """A logical ingestion/batch identity already contains different content."""

    default_message = "The staging batch identity already contains different source content."
    code = "staging_conflict"


class StagingIntegrityError(ETLError):
    """Invalid staging input, sequence or persisted representation/accounting."""

    default_message = "The staging data failed an integrity check."
    code = "staging_integrity_error"


class StagingBatchNotFoundError(ETLError):
    """Raised when the requested ingestion/batch identity has not been staged."""

    default_message = "The requested staging batch does not exist."
    code = "staging_batch_not_found"


class AttemptRegistrationError(ETLError):
    """Invalid attempt operation input or missing selected identity."""

    default_message = "The transformation attempt request is invalid."
    code = "attempt_registration_error"


class AttemptConflictError(ETLError):
    """An attempt identity, lifecycle transition or terminal retry conflicts."""

    default_message = "The transformation attempt conflicts with its recorded state."
    code = "attempt_conflict"


class AttemptIntegrityError(ETLError):
    """Stored metadata, supplied interpretation or result lineage is inconsistent."""

    default_message = "The transformation attempt failed an integrity check."
    code = "attempt_integrity_error"


class TypeConversionError(ETLError):
    """Raised when a non-null value cannot be converted to the target type.

    Carries enough context for diagnostics without exposing unbounded data
    in log messages: source name, batch number, target column, row/index
    location, and a safely truncated representation of the bad value.
    """

    default_message = "A value could not be converted to the target type."
    code = "type_conversion_error"

    def __init__(
        self,
        *,
        source_name: str,
        batch_number: int,
        column: str,
        row_index,
        bad_value,
        reason: str,
        cause: BaseException | None = None,
    ):
        bad_repr = repr(bad_value)
        if len(bad_repr) > 200:
            bad_repr = bad_repr[:197] + "..."
        message = (
            f"{source_name} batch {batch_number}, column {column!r}, "
            f"row index {row_index}: cannot convert {bad_repr} — {reason}"
        )
        super().__init__(message)
        if cause is not None:
            self.__cause__ = cause
