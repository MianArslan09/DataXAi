"""Source verification for the project's dedicated PostgreSQL retail fixture."""

import csv
import os
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from decouple import config
from django.conf import settings
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, OperationalError

from config.source_database import test_source_database_url as resolve_test_url
from config.source_database import validate_test_source_url
from etl.exceptions import SourceConnectionError
from etl.sources import CANONICAL_COLUMNS, PostgresSource

POSTGRES_SKIP_REASON = (
    "Source PostgreSQL unavailable; check TEST_SOURCE_DATABASE_URL or SOURCE_POSTGRES_* "
    "and server availability (credentials withheld)"
)
_TARGET_ERROR = "Required source tests need the explicit local seeded test target; URL withheld."
_SEED_ERROR = "Required PostgreSQL retail fixture identity, permissions or contents are incorrect."
_FIXTURE = Path(__file__).parent / "fixtures" / "online_retail_ii_sample.csv"


def _approved_source_url(explicit):
    if not explicit:
        raise ValueError(_TARGET_ERROR)
    validate_test_source_url(explicit)
    url = make_url(explicit)
    if (
        url.drivername != "postgresql+psycopg"
        or url.host not in {"localhost", "127.0.0.1", "::1"}
        or url.port != 5433
        or url.database != "dataxai_source"
        or url.username != "dataxai_source"
        or url.query
        or any(os.environ.get(key) for key in ("PGHOSTADDR", "PGSERVICE", "PGOPTIONS"))
    ):
        raise ValueError(_TARGET_ERROR)
    return explicit


def required_source_url():
    return _approved_source_url(config("TEST_SOURCE_DATABASE_URL", default=""))


def _required():
    return os.environ.get("DATAXAI_REQUIRE_SOURCE_POSTGRES") == "1"


SOURCE_DB_URL = (
    required_source_url()
    if _required()
    else validate_test_source_url(resolve_test_url(config, settings.SOURCE_DATABASE_URL))
)


def retail_row(row):
    """Comparable values for the fixture's eight columns across CSV, SQL and pandas."""
    invoice, stock, description, quantity, date, price, customer, country = row
    return (
        str(invoice),
        str(stock),
        None if pd.isna(description) or description == "" else description,
        Decimal(str(quantity)),
        pd.Timestamp(date),
        Decimal(str(price)),
        None if pd.isna(customer) or customer == "" else Decimal(str(customer)),
        country,
    )


def expected_retail_rows():
    with _FIXTURE.open(encoding="utf-8", newline="") as stream:
        reader = csv.reader(stream)
        assert tuple(next(reader)) == CANONICAL_COLUMNS
        return Counter(retail_row(row) for row in reader)


def _verify_seed(conn):
    # Match the adapter's unqualified relation and reject search-path shadowing.
    identity = conn.execute(
        text(
            "SELECT current_database(), current_user, "
            "to_regclass('raw_transactions') = to_regclass('public.raw_transactions')"
        )
    ).one()
    if tuple(identity) != ("dataxai_source", "dataxai_source", True):
        pytest.fail(_SEED_ERROR, pytrace=False)
    columns = ", ".join(f'"{name}"' for name in CANONICAL_COLUMNS)
    rows = conn.execute(text(f"SELECT {columns} FROM public.raw_transactions LIMIT 19"))
    try:
        observed = Counter(retail_row(row) for row in rows)
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        observed = None
    if observed != expected_retail_rows():
        pytest.fail(_SEED_ERROR, pytrace=False)


def _probe(url, *, required):
    engine = create_engine(url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as conn:
            if required:
                _verify_seed(conn)
            else:
                conn.execute(text("SELECT 1"))
        return True
    except DBAPIError as exc:
        if not required and not isinstance(exc, OperationalError):
            raise
    finally:
        engine.dispose()
    if required:
        pytest.fail(POSTGRES_SKIP_REASON, pytrace=False)
    return False


def _postgres_available() -> bool:
    if _required():
        return _probe(required_source_url(), required=True)
    return _probe(SOURCE_DB_URL, required=False)


class _SourceUnavailable:
    # Evaluate this shared marker only for selected tests. test_sources_contract
    # also calls _postgres_available() explicitly during its own collection.
    def __bool__(self):
        return not _postgres_available()


requires_postgres = pytest.mark.skipif(_SourceUnavailable(), reason=POSTGRES_SKIP_REASON)


class TestSourceVerificationPolicy:
    def test_seed_checks_multiplicity(self):
        with _FIXTURE.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.reader(stream))[1:]
        assert rows[0] == rows[-1] != rows[1]
        identity = MagicMock()
        identity.one.return_value = ("dataxai_source", "dataxai_source", True)
        conn = MagicMock()
        # Same size, same distinct rows; only their multiplicities change.
        conn.execute.side_effect = [identity, rows, identity, [*rows[:-1], rows[1]]]
        _verify_seed(conn)
        with pytest.raises(pytest.fail.Exception) as caught:
            _verify_seed(conn)
        assert str(caught.value) == _SEED_ERROR

    @pytest.mark.parametrize("required", [False, True])
    @pytest.mark.parametrize("reachable", [False, True])
    def test_probe(self, required, reachable, monkeypatch):
        monkeypatch.setenv("DATAXAI_REQUIRE_SOURCE_POSTGRES", str(int(required)))
        monkeypatch.setenv(
            "TEST_SOURCE_DATABASE_URL",
            "postgresql+psycopg://dataxai_source:private@localhost:5433/dataxai_source",
        )
        for key in ("PGHOSTADDR", "PGSERVICE", "PGOPTIONS"):
            monkeypatch.delenv(key, raising=False)
        secret = "private-probe-token"
        engine = MagicMock()
        if not reachable:
            engine.connect.side_effect = OperationalError(secret, {}, Exception(secret))
        with (
            patch(__name__ + ".create_engine", return_value=engine),
            patch(__name__ + "._verify_seed") as seed,
        ):
            if required and not reachable:
                with pytest.raises(pytest.fail.Exception) as caught:
                    bool(_SourceUnavailable())
                assert str(caught.value) == POSTGRES_SKIP_REASON
                assert caught.value.__context__ is None
            else:
                assert bool(_SourceUnavailable()) is (not reachable)
            assert seed.call_count == int(reachable and required)
        engine.dispose.assert_called_once_with()

    @pytest.mark.parametrize(
        "target",
        [
            "",
            "invalid",
            "postgresql+psycopg://dataxai_source:private@remote:5433/dataxai_source",
            "postgresql+psycopg://dataxai_source:private@localhost:5432/dataxai_source",
            "postgresql+psycopg://dataxai_source:private@localhost:5433/other",
            "postgresql+psycopg://dataxai_source:private@localhost:5433/dataxai_source?host=remote",
        ],
    )
    def test_reject_target_before_connect(self, target, monkeypatch):
        monkeypatch.setenv("TEST_SOURCE_DATABASE_URL", target)
        with patch(__name__ + ".create_engine") as engine, pytest.raises(ValueError) as caught:
            required_source_url()
        engine.assert_not_called()
        assert str(caught.value) in {
            _TARGET_ERROR,
            "Invalid PostgreSQL source test configuration; URL withheld",
        }


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
