from pathlib import Path

import pytest

from etl.exceptions import MalformedSourceError, MissingColumnsError, SourceNotFoundError
from etl.sources import CANONICAL_COLUMNS, CsvSource

FIXTURES = Path(__file__).parent / "fixtures"


class TestCsvSource:
    def test_valid_csv_yields_batches_with_canonical_columns(self):
        with CsvSource(FIXTURES / "online_retail_ii_sample.csv", batch_size=5) as source:
            batches = list(source.extract())
        assert len(batches) == 4  # 18 rows, batch_size=5 -> chunks of 5,5,5,3 = 4 batches
        total_rows = sum(len(b) for b in batches)
        assert total_rows == 18
        for batch in batches:
            assert list(batch.data.columns) == list(CANONICAL_COLUMNS)
            assert batch.source_name == "csv:online_retail_ii_sample.csv"

    def test_missing_file_raises_source_not_found(self):
        with pytest.raises(SourceNotFoundError):
            with CsvSource(FIXTURES / "does_not_exist.csv") as source:
                list(source.extract())

    def test_malformed_csv_raises_malformed_source_error(self):
        with pytest.raises(MalformedSourceError):
            with CsvSource(FIXTURES / "malformed.csv") as source:
                list(source.extract())

    def test_missing_required_columns_raises(self):
        with pytest.raises(MissingColumnsError):
            with CsvSource(FIXTURES / "missing_columns.csv") as source:
                list(source.extract())

    def test_context_manager_cleans_up_reader(self):
        source = CsvSource(FIXTURES / "online_retail_ii_sample.csv")
        with source:
            assert source._reader is not None
        assert source._reader is None

    def test_extract_without_context_manager_raises(self):
        source = CsvSource(FIXTURES / "online_retail_ii_sample.csv")
        with pytest.raises(RuntimeError):
            list(source.extract())
