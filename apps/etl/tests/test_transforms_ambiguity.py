from datetime import datetime, timezone

import pandas as pd
import pytest

from etl.exceptions import AmbiguousColumnsError, MissingColumnsError
from etl.sources import SourceBatch
from etl.transforms import TARGET_COLUMNS, AliasMapper


def _frame():
    return pd.DataFrame(
        {
            "Invoice": ["I2", "I1"],
            "StockCode": ["B", "A"],
            "Description": ["Second", "First"],
            "Quantity": [2, -1],
            "InvoiceDate": pd.to_datetime(["2011-01-02", "2011-01-01"]),
            "Price": [2.5, 1.25],
            "Customer ID": [2002.0, 1001.0],
            "Country": ["UK", "FR"],
        },
        index=[8, 3],
    )


def _batch(data):
    return SourceBatch(
        data=data,
        source_name="csv:transactions.csv",
        batch_number=7,
        extracted_at=datetime(2026, 10, 11, tzinfo=timezone.utc),
    )


def test_duplicate_invoice_labels_raise_with_source_and_positions():
    frame = _frame()
    duplicated = pd.concat([frame.iloc[:, [0]], frame], axis=1)
    before = duplicated.copy(deep=True)

    with pytest.raises(AmbiguousColumnsError) as exc_info:
        AliasMapper().transform(_batch(duplicated))

    message = str(exc_info.value)
    assert "csv:transactions.csv batch 7" in message
    assert "'Invoice'" in message
    assert "zero-based positions [0, 1]" in message
    pd.testing.assert_frame_equal(duplicated, before)


def test_duplicate_other_required_source_label_raises():
    frame = _frame()
    duplicated = pd.concat([frame, frame.loc[:, ["StockCode"]]], axis=1)

    with pytest.raises(AmbiguousColumnsError, match="'StockCode'"):
        AliasMapper().transform(_batch(duplicated))


def test_duplicate_unrelated_extra_labels_are_dropped():
    frame = _frame()
    extras = pd.DataFrame(
        [["left", "right"], ["up", "down"]],
        columns=["extra", "extra"],
        index=frame.index,
    )
    input_frame = pd.concat([frame, extras], axis=1)

    transformed = AliasMapper().transform(_batch(input_frame))

    assert tuple(transformed.data.columns) == TARGET_COLUMNS
    assert len(transformed.data) == len(frame)


def test_extra_alias_target_name_is_dropped_without_collision():
    frame = _frame()
    frame["invoice_no"] = ["ignored-1", "ignored-2"]

    transformed = AliasMapper().transform(_batch(frame))

    assert tuple(transformed.data.columns) == TARGET_COLUMNS
    assert transformed.data["invoice_no"].tolist() == frame["Invoice"].tolist()


def test_missing_required_column_keeps_existing_exception():
    frame = _frame().drop(columns=["Price"])

    with pytest.raises(MissingColumnsError, match="Price"):
        _batch(frame)


def test_valid_input_preserves_schema_values_dtypes_order_index_and_metadata():
    frame = _frame()
    before = frame.copy(deep=True)
    batch = _batch(frame)

    transformed = AliasMapper().transform(batch)

    expected = frame.rename(
        columns={
            "Invoice": "invoice_no",
            "StockCode": "stock_code",
            "Description": "description",
            "Quantity": "quantity",
            "InvoiceDate": "invoice_date",
            "Price": "unit_price",
            "Customer ID": "external_customer_id",
            "Country": "country",
        }
    ).loc[:, list(TARGET_COLUMNS)]
    pd.testing.assert_frame_equal(transformed.data, expected, check_dtype=True)
    assert tuple(transformed.data.columns) == TARGET_COLUMNS
    assert transformed.data["quantity"].tolist() == [2, -1]
    assert transformed.data["unit_price"].tolist() == [2.5, 1.25]
    assert transformed.data["external_customer_id"].tolist() == [2002.0, 1001.0]
    assert transformed.data.index.equals(frame.index)
    assert transformed.source_name == batch.source_name
    assert transformed.batch_number == batch.batch_number
    assert transformed.extracted_at == batch.extracted_at
    pd.testing.assert_frame_equal(frame, before)
