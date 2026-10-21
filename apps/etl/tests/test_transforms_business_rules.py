"""BR-001 through BR-004 and preservation at the typed enrichment boundary."""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from etl.exceptions import AmbiguousColumnsError, ETLError, MissingColumnsError
from etl.sources import CsvSource, ExcelSource
from etl.transforms import (
    TARGET_COLUMNS,
    AliasMapper,
    BusinessRules,
    TransformedBatch,
    TypeConverter,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _batch(invoices, *, index=None):
    count = len(invoices)
    frame = pd.DataFrame(
        {
            # Object dtype deliberately permits malformed values for boundary tests.
            "invoice_no": pd.Series(invoices, dtype=object),
            "stock_code": pd.array(["M"] * count, dtype="string"),
            "description": pd.array([" Manual "] * count, dtype="string"),
            "quantity": pd.array([1] * count, dtype="Int64"),
            "invoice_date": pd.array(["2010-02-01 08:24:00"] * count, dtype="datetime64[ns]"),
            "unit_price": pd.Series([Decimal("10.0000")] * count, dtype=object),
            "external_customer_id": pd.array([pd.NA] * count, dtype="string"),
            "country": pd.array(["United Kingdom"] * count, dtype="string"),
        }
    )
    if index is not None:
        frame.index = index
    return TransformedBatch(
        data=frame,
        source_name="csv:business-rules.csv",
        batch_number=7,
        extracted_at=datetime(2026, 10, 19, 8, 0, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize(
    ("invoice", "quantity", "expected"),
    [
        ("C489449", -12, True),
        ("C496350", 1, True),
        ("C123", pd.NA, True),
        ("489464", -96, False),
        ("489434", 12, False),
        ("c123", -1, False),
        (" C123", -1, False),
        ("\tC123", -1, False),
        ("C123 ", 1, True),
        ("C", 0, True),
        ("AC123", -1, False),
        ("00123", 1, False),
    ],
)
def test_strict_prefix_is_independent_of_quantity(invoice, quantity, expected):
    batch = _batch([invoice])
    batch.data["quantity"] = pd.array([quantity], dtype="Int64")
    before = batch.data.copy(deep=True)

    out = BusinessRules().apply(batch)

    assert bool(out.data["is_cancellation"].iloc[0]) is expected
    assert out.data["is_cancellation"].dtype == pd.BooleanDtype()
    pd.testing.assert_frame_equal(out.data.iloc[:, :-1], before)
    pd.testing.assert_frame_equal(batch.data, before)


@pytest.mark.parametrize(
    "invoice", [None, pd.NA, "", "   ", "\t\r\n", "\u00a0", float("nan"), pd.NaT, Decimal("NaN")]
)
def test_null_or_blank_invoice_retains_row_and_original_value(invoice):
    batch = _batch([invoice])
    before = batch.data.copy(deep=True)

    out = BusinessRules().apply(batch)

    assert len(out) == 1
    assert out.data["is_cancellation"].iloc[0] is pd.NA
    assert out.data["is_cancellation"].dtype == pd.BooleanDtype()
    assert out.data["invoice_no"].iloc[0] is batch.data["invoice_no"].iloc[0]
    pd.testing.assert_frame_equal(out.data.iloc[:, :-1], before)
    pd.testing.assert_frame_equal(batch.data, before)


@pytest.mark.parametrize(
    "invoice",
    [
        123,
        123.0,
        True,
        False,
        [],
        ["C123"],
        {},
        {"invoice": "C123"},
        np.array(["C123"]),
        Decimal("123"),
        Decimal("sNaN"),
        b"C123",
        pd.Timestamp("2010-01-01"),
    ],
    ids=[
        "integer",
        "float",
        "true",
        "false",
        "empty-list",
        "list",
        "empty-dict",
        "dict",
        "array",
        "decimal",
        "signaling-nan",
        "bytes",
        "timestamp",
    ],
)
def test_invalid_types_fail_atomically_with_row_context(invoice):
    batch = _batch(["C123", invoice, "489434"], index=["first", "bad-row", "last"])
    before = batch.data.copy(deep=True)
    invoice_reprs = batch.data["invoice_no"].map(repr)

    with pytest.raises(ETLError) as exc_info:
        BusinessRules().apply(batch)

    message = str(exc_info.value)
    assert "csv:business-rules.csv batch 7" in message
    assert "column 'invoice_no', row index bad-row" in message
    assert repr(invoice) in message
    assert "requires a string or null after TypeConverter" in message
    # Comparing sNaN with pandas can itself raise InvalidOperation. Check its
    # identity and the original representations without invoking numeric equality.
    assert batch.data["invoice_no"].iloc[1] is invoice
    pd.testing.assert_series_equal(batch.data["invoice_no"].map(repr), invoice_reprs)
    pd.testing.assert_series_equal(batch.data.dtypes, before.dtypes)
    pd.testing.assert_frame_equal(
        batch.data.drop(columns="invoice_no"), before.drop(columns="invoice_no")
    )
    assert "is_cancellation" not in batch.data.columns


def test_invalid_value_representation_is_bounded():
    invoice = {"payload": "x" * 1000}
    batch = _batch([invoice])
    with pytest.raises(ETLError) as exc_info:
        BusinessRules().apply(batch)
    message = str(exc_info.value)
    assert repr(invoice)[:197] + "..." in message
    assert "x" * 201 not in message
    assert batch.data["invoice_no"].iloc[0] is invoice


@pytest.mark.parametrize("existing", [True, False, pd.NA])
def test_existing_marker_is_never_overwritten(existing):
    batch = _batch(["C123"])
    batch.data["is_cancellation"] = pd.array([existing], dtype="boolean")
    before = batch.data.copy(deep=True)
    with pytest.raises(AmbiguousColumnsError, match="batch 7.*already contains 'is_cancellation'"):
        BusinessRules().apply(batch)
    pd.testing.assert_frame_equal(batch.data, before)


@pytest.mark.parametrize("column", ["invoice_no", "quantity"])
def test_missing_column_is_rechecked_after_batch_construction(column):
    batch = _batch(["C123"])
    batch.data.drop(columns=column, inplace=True)
    before = batch.data.copy(deep=True)
    with pytest.raises(MissingColumnsError, match=f"batch 7.*{column}"):
        BusinessRules().apply(batch)
    pd.testing.assert_frame_equal(batch.data, before)


def test_duplicate_invoice_columns_are_rejected_at_point_of_use():
    batch = _batch(["C123", "489434"])
    batch.data.insert(1, "invoice_no", ["489434", "C123"], allow_duplicates=True)
    before = batch.data.copy(deep=True)
    with pytest.raises(AmbiguousColumnsError) as exc_info:
        BusinessRules().apply(batch)
    message = str(exc_info.value)
    assert "csv:business-rules.csv batch 7" in message
    assert "'invoice_no' at zero-based positions [0, 1]" in message
    pd.testing.assert_frame_equal(batch.data, before)


def test_empty_batch_retains_schema_index_and_metadata():
    batch = _batch([], index=pd.Index([], dtype="int64", name="source_row"))
    before = batch.data.copy(deep=True)
    out = BusinessRules().apply(batch)
    assert len(out) == 0
    assert list(out.data.columns) == [*TARGET_COLUMNS, "is_cancellation"]
    assert out.data["is_cancellation"].dtype == pd.BooleanDtype()
    pd.testing.assert_frame_equal(out.data.iloc[:, :-1], before)
    pd.testing.assert_frame_equal(batch.data, before)
    assert (out.source_name, out.batch_number, out.extracted_at) == (
        batch.source_name,
        batch.batch_number,
        batch.extracted_at,
    )


@pytest.mark.parametrize(
    "index",
    [pd.Index([8, 3, 1], name="source_row"), pd.Index([8, 8, 3], name="source_row")],
    ids=["non-default", "duplicate"],
)
def test_preserves_all_columns_dtypes_values_index_and_metadata(index):
    batch = _batch(["C123", "489464", None], index=index)
    prices = [Decimal("10.0000"), Decimal("-0.0000"), Decimal("-53594.3600")]
    batch.data["unit_price"] = prices
    batch.data["quantity"] = pd.array([1, -96, pd.NA], dtype="Int64")
    batch.data["invoice_date"] = pd.array(
        ["2010-02-01 08:24:00.123456789", None, "2009-12-01 10:52:00"], dtype="datetime64[ns]"
    )
    batch.data["extra"] = pd.Categorical(["a", "b", "a"])
    batch.data.insert(2, "extra", ["left", "right", "last"], allow_duplicates=True)
    before = batch.data.copy(deep=True)

    out = BusinessRules().apply(batch)

    assert out is not batch and out.data is not batch.data
    assert list(out.data.columns) == [*before.columns, "is_cancellation"]
    pd.testing.assert_frame_equal(out.data.iloc[:, :-1], before, check_exact=True)
    pd.testing.assert_frame_equal(batch.data, before, check_exact=True)
    pd.testing.assert_series_equal(
        out.data["is_cancellation"],
        pd.Series([True, False, pd.NA], dtype="boolean", index=index, name="is_cancellation"),
    )
    for actual, original in zip(out.data["unit_price"], prices, strict=True):
        assert actual is original
        assert actual.as_tuple() == original.as_tuple()
    assert (out.source_name, out.batch_number, out.extracted_at) == (
        batch.source_name,
        batch.batch_number,
        batch.extracted_at,
    )
    out.data.iloc[0, 0] = "changed-output"
    pd.testing.assert_frame_equal(batch.data, before)


def test_equivalent_inputs_are_deterministic_and_reenrichment_is_rejected():
    rules = BusinessRules()
    first = rules.apply(_batch(["C123", None, "489434"]))
    second = rules.apply(_batch(["C123", None, "489434"]))
    pd.testing.assert_frame_equal(first.data, second.data)
    with pytest.raises(AmbiguousColumnsError, match="is_cancellation"):
        rules.apply(first)


@pytest.mark.parametrize(
    ("source_type", "filename", "batch_lengths"),
    [
        (CsvSource, "online_retail_ii_sample.csv", [5, 5, 5, 3]),
        (ExcelSource, "online_retail_ii_sample.xlsx", [5, 4, 5, 4]),
    ],
    ids=["csv", "excel"],
)
def test_fixture_pipeline_preserves_every_row_value_and_batch(source_type, filename, batch_lengths):
    outputs = []
    with source_type(FIXTURES / filename, batch_size=5) as source:
        for raw in source.extract():
            raw_before = raw.data.copy(deep=True)
            typed = TypeConverter().convert(AliasMapper().transform(raw))
            before = typed.data.copy(deep=True)

            out = BusinessRules().apply(typed)

            pd.testing.assert_frame_equal(raw.data, raw_before)
            pd.testing.assert_frame_equal(typed.data, before)
            pd.testing.assert_frame_equal(out.data.iloc[:, :-1], before, check_exact=True)
            assert (out.source_name, out.batch_number, out.extracted_at) == (
                raw.source_name,
                raw.batch_number,
                raw.extracted_at,
            )
            assert len(out) == len(raw)
            outputs.append(out)

    assert [len(batch) for batch in outputs] == batch_lengths
    result = pd.concat([batch.data for batch in outputs], ignore_index=True)
    assert len(result) == 18
    expected = [False] * 7 + [
        True,
        True,
        True,
        False,
        False,
        False,
        True,
        True,
        False,
        False,
        False,
    ]
    pd.testing.assert_series_equal(
        result["is_cancellation"], pd.Series(expected, dtype="boolean", name="is_cancellation")
    )
    assert result["is_cancellation"].sum() == 5
    assert (~result["is_cancellation"]).sum() == 13
    assert result["is_cancellation"].isna().sum() == 0
    assert result.duplicated().sum() == 1
    assert (result["quantity"] < 0).sum() == 7
    assert (result["unit_price"] < 0).sum() == 1
    assert result["external_customer_id"].isna().sum() == 7
    assert result["description"].isna().sum() == 2
    assert result.loc[result["stock_code"].isin(["POST", "D", "M", "B"])].shape[0] == 5
