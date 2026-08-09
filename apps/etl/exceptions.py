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


class SourceConnectionError(ETLError):
    default_message = "Could not connect to the source."
    code = "source_connection_error"
