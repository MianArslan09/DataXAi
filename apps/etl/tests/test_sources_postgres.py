"""Real PostgreSQL fixtures via TEST_SOURCE_DATABASE_URL or configured source URL."""

import pytest
from decouple import config
from django.conf import settings
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from config.source_database import test_source_database_url as resolve_test_url
from config.source_database import validate_test_source_url
from etl.exceptions import SourceConnectionError
from etl.sources import CANONICAL_COLUMNS, PostgresSource

SOURCE_DB_URL = validate_test_source_url(resolve_test_url(config, settings.SOURCE_DATABASE_URL))
POSTGRES_SKIP_REASON = (
    "Source PostgreSQL unavailable; check TEST_SOURCE_DATABASE_URL or SOURCE_POSTGRES_* "
    "and server availability (credentials withheld)"
)


def _postgres_available() -> bool:
    engine = create_engine(SOURCE_DB_URL, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False
    finally:
        engine.dispose()


requires_postgres = pytest.mark.skipif(not _postgres_available(), reason=POSTGRES_SKIP_REASON)


@requires_postgres
class TestPostgresSource:
    def test_valid_query_yields_batches_with_canonical_columns(self):
        with PostgresSource(SOURCE_DB_URL, table="raw_transactions", batch_size=5) as source:
            batches = list(source.extract())
        total_rows = sum(len(b) for b in batches)
        assert total_rows == 18  # matches the seeded raw_transactions row count
        for batch in batches:
            assert list(batch.data.columns) == list(CANONICAL_COLUMNS)
            assert batch.source_name == "postgres:raw_transactions"

    def test_batching_respects_batch_size(self):
        with PostgresSource(SOURCE_DB_URL, table="raw_transactions", batch_size=5) as source:
            batches = list(source.extract())
        assert len(batches) == 4  # 18 rows, batch_size=5 -> 5,5,5,3
        assert [len(b) for b in batches[:3]] == [5, 5, 5]

    def test_connection_lifecycle_engine_and_connection_cleaned_up(self):
        source = PostgresSource(SOURCE_DB_URL, table="raw_transactions")
        with source:
            assert source._engine is not None
            assert source._connection is not None
        assert source._engine is None
        assert source._connection is None

    def test_connection_failure_raises_source_connection_error(self):
        bad_url = "postgresql+psycopg://wrong:wrong@localhost:59999/nonexistent"
        with pytest.raises(SourceConnectionError):
            with PostgresSource(bad_url) as source:
                list(source.extract())

    def test_extract_without_context_manager_raises(self):
        source = PostgresSource(SOURCE_DB_URL, table="raw_transactions")
        with pytest.raises(RuntimeError):
            list(source.extract())
