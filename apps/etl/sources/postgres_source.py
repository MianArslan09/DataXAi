"""
PostgreSQL Source - reads from a SEPARATE, external/upstream database via
SQLAlchemy and SOURCE_DATABASE_URL. Never Django's ORM, never the
DATABASE_URL/`default` connection - that's DataXAi's own application
database and must stay fully isolated from whatever an SME's existing
system looks like (Volume 4.1 architectural decision).

Known limitation (documented, not hidden): this expects the source
table to already be shaped to CANONICAL_COLUMNS. Mapping an arbitrary
real-world upstream schema is future work - see Volume 4.2 deferred
scope.
"""

import logging
from typing import Iterator

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.exc import InterfaceError, OperationalError, SQLAlchemyError

from etl.exceptions import ETLError, MissingColumnsError, SourceConnectionError
from etl.sources._cleanup import cleanup_on_exit, close_resources
from etl.sources.contract import CANONICAL_COLUMNS, SourceBatch

logger = logging.getLogger(__name__)


class PostgresSource:
    """Usage: `with PostgresSource(url) as source: for batch in source.extract(): ...`"""

    def __init__(
        self, connection_url: str, table: str = "raw_transactions", batch_size: int = 50_000
    ):
        self.connection_url = connection_url
        self.table = table
        self.batch_size = batch_size
        self._engine = None
        self._connection = None

    def __enter__(self) -> "PostgresSource":
        try:
            self._engine = create_engine(self.connection_url)
            self._connection = self._engine.connect()
            self._connection = self._connection.execution_options(stream_results=True)
            self._connection.execute(text("SELECT 1"))
        except BaseException as exc:
            self._cleanup(primary_error=exc)
            if isinstance(exc, SQLAlchemyError):
                raise _database_error(exc, connecting=True) from exc
            raise
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._cleanup(primary_error=exc_val)

    def _cleanup(self, *, primary_error=None) -> None:
        connection, self._connection = self._connection, None
        engine, self._engine = self._engine, None
        callbacks = []
        if connection is not None:
            callbacks.append(connection.close)
        if engine is not None:
            callbacks.append(engine.dispose)
        close_resources(*callbacks, primary_error=primary_error)

    def extract(self) -> Iterator[SourceBatch]:
        if self._connection is None:
            raise RuntimeError(
                "PostgresSource must be used as a context manager: `with PostgresSource(...) as s:`"
            )
        columns_sql = ", ".join(f'"{c}"' for c in CANONICAL_COLUMNS)
        # self.table is server-side configuration (from settings/env), never
        # end-user input, so this is not a SQL-injection vector - it's still
        # not parameterizable because table names can't be bind parameters
        # in standard SQL.
        query = text(f'SELECT {columns_sql} FROM "{self.table}"')
        batch_number = 0
        try:
            chunks = pd.read_sql(query, self._connection, chunksize=self.batch_size)
            with cleanup_on_exit(chunks.close):
                for chunk in chunks:
                    missing = set(CANONICAL_COLUMNS) - set(chunk.columns)
                    if missing:
                        raise MissingColumnsError(
                            f"Source database is missing columns: {sorted(missing)}"
                        )
                    logger.info(
                        "postgres_source.batch",
                        extra={"batch": batch_number, "rows": len(chunk)},
                    )
                    yield SourceBatch(
                        data=chunk,
                        source_name=f"postgres:{self.table}",
                        batch_number=batch_number,
                    )
                    batch_number += 1
        except SQLAlchemyError as exc:
            raise _database_error(exc, connecting=False) from exc


def _database_error(exc, *, connecting):
    # Lazy import avoids the classifier -> snapshots -> sources import cycle.
    from etl.error_handling import FailureReason, classify_etl_failure

    failure = classify_etl_failure(exc)
    connection_failure = failure.reason == FailureReason.CONNECTION_INTERRUPTED or (
        connecting
        and isinstance(exc, (OperationalError, InterfaceError))
        and failure.reason == FailureReason.UNSPECIFIED
    )
    error = SourceConnectionError if connection_failure else ETLError
    return error(failure.message)
