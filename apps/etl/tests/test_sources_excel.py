from pathlib import Path

import pytest

from etl.exceptions import MalformedSourceError, SourceNotFoundError
from etl.sources import CANONICAL_COLUMNS, ExcelSource

FIXTURES = Path(__file__).parent / "fixtures"


class TestExcelSource:
    def test_valid_workbook_reads_all_sheets_by_default(self):
        with ExcelSource(FIXTURES / "online_retail_ii_sample.xlsx", batch_size=5) as source:
            assert source.sheet_names == ["Year 2009-2010", "Year 2010-2011"]
            batches = list(source.extract())
        total_rows = sum(len(b) for b in batches)
        assert total_rows == 18  # 9 rows/sheet x 2 sheets, matching the fixture split
        for batch in batches:
            assert list(batch.data.columns) == list(CANONICAL_COLUMNS)

    def test_selected_sheet_reads_only_that_sheet(self):
        with ExcelSource(
            FIXTURES / "online_retail_ii_sample.xlsx", sheet_names=["Year 2009-2010"], batch_size=5
        ) as source:
            batches = list(source.extract())
        assert sum(len(b) for b in batches) == 9
        assert all(b.source_name.endswith("Year 2009-2010") for b in batches)

    def test_batching_within_a_sheet(self):
        """batch_size smaller than a single sheet's row count must still
        split correctly, not just work when batch_size >= sheet size."""
        with ExcelSource(
            FIXTURES / "online_retail_ii_sample.xlsx", sheet_names=["Year 2009-2010"], batch_size=4
        ) as source:
            batches = list(source.extract())
        assert len(batches) == 3  # 9 rows, batch_size=4 -> 4,4,1
        assert [len(b) for b in batches] == [4, 4, 1]

    def test_missing_workbook_raises_source_not_found(self):
        with pytest.raises(SourceNotFoundError):
            with ExcelSource(FIXTURES / "does_not_exist.xlsx") as source:
                list(source.extract())

    def test_invalid_workbook_raises_malformed_source_error(self):
        with pytest.raises(MalformedSourceError):
            # a CSV, not a real xlsx
            with ExcelSource(FIXTURES / "online_retail_ii_sample.csv") as source:
                list(source.extract())

    def test_unknown_sheet_name_raises_malformed_source_error(self):
        with pytest.raises(MalformedSourceError):
            with ExcelSource(
                FIXTURES / "online_retail_ii_sample.xlsx", sheet_names=["Nonexistent Sheet"]
            ):
                pass

    def test_context_manager_cleans_up_workbook(self):
        source = ExcelSource(FIXTURES / "online_retail_ii_sample.xlsx")
        with source:
            assert source._workbook is not None
        assert source._workbook is None
