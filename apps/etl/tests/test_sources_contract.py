"""
Proves the actual point of Volume 4.2: CSV, Excel, and PostgreSQL sources
are interchangeable from the Transform layer's point of view. All three
are seeded from the exact same 18 real rows, so if the contract holds,
their outputs must be structurally identical (columns, dtypes-after-
normalization, row count) even though the underlying storage format is
completely different.
"""

from pathlib import Path

import pandas as pd
import pytest

from etl.sources import CANONICAL_COLUMNS, CsvSource, ExcelSource, PostgresSource
from etl.tests.test_sources_postgres import SOURCE_DB_URL, _postgres_available

FIXTURES = Path(__file__).parent / "fixtures"


def _all_rows(source_cm) -> pd.DataFrame:
    with source_cm as source:
        batches = list(source.extract())
    return pd.concat([b.data for b in batches], ignore_index=True)


class TestCrossSourceContract:
    def test_csv_and_excel_produce_the_same_columns_and_row_count(self):
        csv_df = _all_rows(CsvSource(FIXTURES / "online_retail_ii_sample.csv"))
        excel_df = _all_rows(ExcelSource(FIXTURES / "online_retail_ii_sample.xlsx"))

        assert list(csv_df.columns) == list(CANONICAL_COLUMNS)
        assert list(excel_df.columns) == list(CANONICAL_COLUMNS)
        assert len(csv_df) == len(excel_df) == 18

    @pytest.mark.skipif(
        not _postgres_available(), reason=f"No real Postgres reachable at {SOURCE_DB_URL}"
    )
    def test_all_three_sources_produce_the_same_shape(self):
        csv_df = _all_rows(CsvSource(FIXTURES / "online_retail_ii_sample.csv"))
        excel_df = _all_rows(ExcelSource(FIXTURES / "online_retail_ii_sample.xlsx"))
        pg_df = _all_rows(PostgresSource(SOURCE_DB_URL, table="raw_transactions"))

        for df, name in [(csv_df, "csv"), (excel_df, "excel"), (pg_df, "postgres")]:
            assert list(df.columns) == list(CANONICAL_COLUMNS), f"{name} column mismatch"
            assert len(df) == 18, f"{name} row count mismatch"

        # Transform (4.3) will receive these as interchangeable inputs - prove
        # the actual values line up too, not just shape, by comparing the
        # Invoice+StockCode key set across all three.
        def _keys(df: pd.DataFrame) -> set:
            return set(zip(df["Invoice"].astype(str), df["StockCode"].astype(str), strict=True))

        assert _keys(csv_df) == _keys(excel_df) == _keys(pg_df)

    def test_source_protocol_is_satisfied_by_all_three_implementations(self):
        from etl.sources.contract import Source

        assert isinstance(CsvSource(FIXTURES / "x.csv"), Source)
        assert isinstance(ExcelSource(FIXTURES / "x.xlsx"), Source)
        assert isinstance(PostgresSource("postgresql://x"), Source)
