"""
Volume 4.3.1 behaviour tests for AliasMapper. The central claim: it
changes column NAMES and nothing else, and it neither knows nor cares
which Source produced the batch. Inputs are real SourceBatch objects
from the real CsvSource / ExcelSource / PostgresSource (18 real UCI rows).
"""

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from etl.exceptions import MissingColumnsError
from etl.sources import CANONICAL_COLUMNS, CsvSource, ExcelSource, PostgresSource, SourceBatch
from etl.tests.test_sources_postgres import SOURCE_DB_URL, requires_postgres
from etl.transforms import ALIAS_MAP, TARGET_COLUMNS, AliasMapper

FIXTURES = Path(__file__).parent / "fixtures"
INVERSE = {target: raw for raw, target in ALIAS_MAP.items()}


def _csv_batches(batch_size=50_000):
    with CsvSource(FIXTURES / "online_retail_ii_sample.csv", batch_size=batch_size) as s:
        return list(s.extract())


def _excel_batches(batch_size=50_000):
    with ExcelSource(FIXTURES / "online_retail_ii_sample.xlsx", batch_size=batch_size) as s:
        return list(s.extract())


def _whole(batches):
    return pd.concat([b.data for b in batches], ignore_index=True)


def _map_all(batches):
    mapper = AliasMapper()
    return [mapper.transform(b) for b in batches]


class TestAliasMapperRenamesOnly:
    @pytest.mark.parametrize("make_batches", [_csv_batches, _excel_batches])
    def test_output_columns_are_exactly_the_target_columns_in_order(self, make_batches):
        for out in _map_all(make_batches()):
            assert list(out.data.columns) == list(TARGET_COLUMNS)

    @pytest.mark.parametrize("make_batches", [_csv_batches, _excel_batches])
    def test_values_dtypes_order_and_index_are_untouched(self, make_batches):
        mapper = AliasMapper()
        for batch in make_batches(batch_size=7):
            out = mapper.transform(batch)
            restored = out.data.rename(columns=INVERSE)
            pd.testing.assert_frame_equal(restored, batch.data, check_dtype=True)
            assert out.data.index.equals(batch.data.index)

    def test_input_dataframe_is_not_mutated(self):
        batch = _csv_batches()[0]
        before = batch.data.copy(deep=True)
        out = AliasMapper().transform(batch)
        pd.testing.assert_frame_equal(batch.data, before)
        assert out.data is not batch.data

    def test_source_metadata_is_carried_through_unchanged(self):
        for batch in _excel_batches(batch_size=7):
            out = AliasMapper().transform(batch)
            assert out.source_name == batch.source_name
            assert out.batch_number == batch.batch_number
            assert out.extracted_at == batch.extracted_at

    def test_metadata_differs_per_batch_and_is_not_collapsed(self):
        outs = _map_all(_excel_batches(batch_size=7))
        assert [o.batch_number for o in outs] == [0, 1, 2, 3]
        assert {o.source_name.rsplit(":", 1)[-1] for o in outs} == {
            "Year 2009-2010",
            "Year 2010-2011",
        }


class TestAliasMapperDoesNotDecideDataQuestions:
    """4.3.1 must not filter, flag or fix anything. The unresolved Vol 4.3
    data cases from the Vol 4.1 inspection must all still be present,
    byte-for-byte, after alias mapping."""

    def setup_method(self):
        self.raw = _whole(_csv_batches())
        self.out = _whole(_map_all(_csv_batches()))

    def test_no_rows_dropped_including_the_deliberate_duplicate(self):
        assert len(self.out) == len(self.raw) == 18
        assert self.out.duplicated().sum() == self.raw.duplicated().sum() == 1

    def test_administrative_stock_codes_are_still_present(self):
        assert {"POST", "D", "M"} <= set(self.out["stock_code"].astype(str))

    def test_negative_price_row_is_still_present(self):
        assert (self.out["unit_price"] < 0).sum() == (self.raw["Price"] < 0).sum() == 1

    def test_positive_quantity_cancellation_is_still_present_unflagged(self):
        row = self.out[self.out["invoice_no"].astype(str) == "C496350"]
        assert len(row) == 1
        assert row["quantity"].iloc[0] > 0
        assert "is_cancellation" not in self.out.columns

    def test_null_customer_ids_and_descriptions_are_not_filled_or_dropped(self):
        assert self.out["external_customer_id"].isna().sum() == self.raw["Customer ID"].isna().sum()
        assert self.out["external_customer_id"].isna().sum() > 0
        assert self.out["description"].isna().sum() == self.raw["Description"].isna().sum()

    def test_dates_are_not_parsed_in_this_subsection(self):
        assert self.out["invoice_date"].dtype == self.raw["InvoiceDate"].dtype


class TestAliasMapperInputHandling:
    def _batch(self, df):
        return SourceBatch(data=df, source_name="test:src", batch_number=0)

    def test_extra_columns_are_dropped(self):
        df = _csv_batches()[0].data.copy()
        df["Unexpected"] = 1
        out = AliasMapper().transform(self._batch(df))
        assert list(out.data.columns) == list(TARGET_COLUMNS)

    def test_input_column_order_does_not_matter(self):
        df = _csv_batches()[0].data
        shuffled = df[list(reversed(CANONICAL_COLUMNS))]
        out = AliasMapper().transform(self._batch(shuffled))
        assert list(out.data.columns) == list(TARGET_COLUMNS)

    def test_column_removed_after_batch_construction_is_still_caught(self):
        batch = self._batch(_csv_batches()[0].data.copy())
        batch.data.drop(columns=["Price"], inplace=True)  # DataFrame is mutable
        with pytest.raises(MissingColumnsError, match="Price"):
            AliasMapper().transform(batch)

    def test_empty_batch_maps_to_an_empty_batch_with_target_columns(self):
        empty = pd.DataFrame(columns=list(CANONICAL_COLUMNS))
        out = AliasMapper().transform(self._batch(empty))
        assert len(out) == 0
        assert list(out.data.columns) == list(TARGET_COLUMNS)


class TestTransformIsSourceAgnostic:
    def _keys(self, df):
        return set(zip(df["invoice_no"].astype(str), df["stock_code"].astype(str), strict=True))

    def test_csv_and_excel_batches_yield_identical_shape_and_keys(self):
        csv_out = _whole(_map_all(_csv_batches()))
        xlsx_out = _whole(_map_all(_excel_batches()))
        assert list(csv_out.columns) == list(xlsx_out.columns) == list(TARGET_COLUMNS)
        assert len(csv_out) == len(xlsx_out) == 18
        assert self._keys(csv_out) == self._keys(xlsx_out)

    @requires_postgres
    def test_postgres_batches_yield_the_same_shape_and_keys_as_csv_and_excel(self):
        with PostgresSource(SOURCE_DB_URL, table="raw_transactions", batch_size=5) as s:
            pg_batches = list(s.extract())
        pg_out = _whole(_map_all(pg_batches))
        csv_out = _whole(_map_all(_csv_batches()))
        assert list(pg_out.columns) == list(TARGET_COLUMNS)
        assert len(pg_out) == 18
        assert self._keys(pg_out) == self._keys(csv_out)
        assert all(m.source_name == "postgres:raw_transactions" for m in _map_all(pg_batches))


def test_extracted_at_is_taken_from_the_source_batch_not_regenerated():
    stamp = datetime(2020, 5, 17, 12, 0, tzinfo=timezone.utc)
    df = _csv_batches()[0].data
    out = AliasMapper().transform(
        SourceBatch(data=df, source_name="t", batch_number=0, extracted_at=stamp)
    )
    assert out.extracted_at == stamp
