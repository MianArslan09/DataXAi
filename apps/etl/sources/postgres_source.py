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
from sqlalchemy.exc import OperationalError, ProgrammingError

from etl.exceptions import MissingColumnsError, SourceConnectionError
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
        self._engine = create_engine(self.connection_url)
        try:
            self._connection = self._engine.connect().execution_options(stream_results=True)
            self._connection.execute(text("SELECT 1"))
        except OperationalError as exc:
            self._cleanup()
            raise SourceConnectionError(f"Could not connect to source database: {exc}") from exc
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._cleanup()

    def _cleanup(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None

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
            for chunk in pd.read_sql(query, self._connection, chunksize=self.batch_size):
                missing = set(CANONICAL_COLUMNS) - set(chunk.columns)
                if missing:
                    raise MissingColumnsError(
                        f"source table '{self.table}' missing columns: {sorted(missing)}"
                    )
                logger.info(
                    "postgres_source.batch",
                    extra={"table": self.table, "batch": batch_number, "rows": len(chunk)},
                )
                yield SourceBatch(
                    data=chunk,
                    source_name=f"postgres:{self.table}",
                    batch_number=batch_number,
                )
                batch_number += 1
        except (OperationalError, ProgrammingError) as exc:
            raise SourceConnectionError(f"Query against source database failed: {exc}") from exc
