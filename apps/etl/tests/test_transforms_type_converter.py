"""
Volume 4.3.2 tests for TypeConverter.

Covers every target column's conversion, null handling, edge cases,
error reporting, input immutability, metadata preservation, empty
batches, determinism, and integration with AliasMapper.  Does not
test validation, healing, or business-rule enforcement.
"""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from etl.exceptions import TypeConversionError
from etl.sources import CANONICAL_COLUMNS, CsvSource, ExcelSource, PostgresSource, SourceBatch
from etl.tests.test_sources_postgres import SOURCE_DB_URL, requires_postgres
from etl.transforms import TARGET_COLUMNS, AliasMapper, TypeConverter

FIXTURES = Path(__file__).parent / "fixtures"


# ─── Helpers ─────────────────────────────────────────────────────────


def _source_batch(
    data_dict,
    *,
    index=None,
    source_name="csv:test.csv",
    batch_number=0,
    extracted_at=datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc),
):
    """Build a SourceBatch → AliasMapper → TransformedBatch for test input."""
    df = pd.DataFrame(data_dict, index=index)
    sb = SourceBatch(
        data=df, source_name=source_name, batch_number=batch_number, extracted_at=extracted_at
    )
    return AliasMapper().transform(sb)


def _good_row(**overrides):
    """Return a dict with valid canonical-column values for one row.

    Accepts overrides for any raw column to test specific edge cases.
    """
    defaults = {
        "Invoice": "489434",
        "StockCode": "79323P",
        "Description": "PINK CHERRY LIGHTS",
        "Quantity": 12,
        "InvoiceDate": "2009-12-01 07:45:00",
        "Price": 6.75,
        "Customer ID": 13085.0,
        "Country": "United Kingdom",
    }
    defaults.update(overrides)
    return defaults


def _single_batch(**overrides):
    """Build a one-row TransformedBatch with good defaults and overrides."""
    row = _good_row(**overrides)
    return _source_batch({k: [v] for k, v in row.items()})


def _convert(batch):
    return TypeConverter().convert(batch)


# ─── String ID Columns ──────────────────────────────────────────────


class TestInvoiceNoConversion:
    def test_string_invoice_no_preserved(self):
        out = _convert(_single_batch(Invoice="489434"))
        assert out.data["invoice_no"].iloc[0] == "489434"

    def test_cancellation_prefix_preserved(self):
        out = _convert(_single_batch(Invoice="C489449"))
        assert out.data["invoice_no"].iloc[0] == "C489449"

    def test_leading_zeros_preserved(self):
        out = _convert(_single_batch(Invoice="00123"))
        assert out.data["invoice_no"].iloc[0] == "00123"

    def test_null_invoice_no_stays_null(self):
        out = _convert(_single_batch(Invoice=None))
        assert pd.isna(out.data["invoice_no"].iloc[0])

    def test_nan_invoice_no_stays_null_not_string(self):
        out = _convert(_single_batch(Invoice=float("nan")))
        assert pd.isna(out.data["invoice_no"].iloc[0])
        assert out.data["invoice_no"].iloc[0] is not "nan"  # noqa: F632

    def test_result_dtype_is_string(self):
        out = _convert(_single_batch())
        assert out.data["invoice_no"].dtype == pd.StringDtype()


class TestStockCodeConversion:
    def test_string_stock_code_preserved(self):
        out = _convert(_single_batch(StockCode="85123A"))
        assert out.data["stock_code"].iloc[0] == "85123A"

    def test_admin_codes_preserved(self):
        for code in ("POST", "D", "M", "B"):
            out = _convert(_single_batch(StockCode=code))
            assert out.data["stock_code"].iloc[0] == code


class TestExternalCustomerIdConversion:
    def test_float_id_becomes_string_without_decimal(self):
        """13085.0 → '13085'"""
        out = _convert(_single_batch(**{"Customer ID": 13085.0}))
        assert out.data["external_customer_id"].iloc[0] == "13085"

    def test_textual_id_with_leading_zeros_preserved(self):
        out = _convert(_single_batch(**{"Customer ID": "00123"}))
        assert out.data["external_customer_id"].iloc[0] == "00123"

    def test_int_id_becomes_string(self):
        out = _convert(_single_batch(**{"Customer ID": 13085}))
        assert out.data["external_customer_id"].iloc[0] == "13085"

    def test_null_customer_id_stays_null(self):
        out = _convert(_single_batch(**{"Customer ID": None}))
        assert pd.isna(out.data["external_customer_id"].iloc[0])

    def test_nan_customer_id_stays_null_not_string_nan(self):
        out = _convert(_single_batch(**{"Customer ID": float("nan")}))
        assert pd.isna(out.data["external_customer_id"].iloc[0])

    def test_fractional_float_id_raises(self):
        with pytest.raises(TypeConversionError, match="non-integral float"):
            _convert(_single_batch(**{"Customer ID": 130.85}))

    def test_inf_id_raises(self):
        with pytest.raises(TypeConversionError, match="non-finite"):
            _convert(_single_batch(**{"Customer ID": float("inf")}))


# ─── Passthrough String Columns ─────────────────────────────────────


class TestDescriptionConversion:
    def test_description_preserved(self):
        out = _convert(_single_batch(Description="PINK CHERRY LIGHTS"))
        assert out.data["description"].iloc[0] == "PINK CHERRY LIGHTS"

    def test_null_description_stays_null(self):
        out = _convert(_single_batch(Description=None))
        assert pd.isna(out.data["description"].iloc[0])

    def test_nan_description_stays_null_not_string_nan(self):
        out = _convert(_single_batch(Description=float("nan")))
        assert pd.isna(out.data["description"].iloc[0])

    def test_description_not_trimmed(self):
        out = _convert(_single_batch(Description="  leading and trailing  "))
        assert out.data["description"].iloc[0] == "  leading and trailing  "


class TestCountryConversion:
    def test_country_preserved(self):
        out = _convert(_single_batch(Country="United Kingdom"))
        assert out.data["country"].iloc[0] == "United Kingdom"

    def test_null_country_stays_null(self):
        out = _convert(_single_batch(Country=None))
        assert pd.isna(out.data["country"].iloc[0])


class TestDecimalNullHandling:
    @pytest.mark.parametrize("column", TARGET_COLUMNS)
    def test_signaling_nan_is_contextual_and_does_not_mutate_input(self, column):
        batch = _source_batch(
            {key: [value] for key, value in _good_row().items()},
            index=[42],
            source_name="decimal-source",
            batch_number=7,
        )
        value = Decimal("sNaN")
        batch.data[column] = pd.Series([value], index=[42], dtype=object)
        before = batch.data.copy(deep=True)

        with pytest.raises(TypeConversionError, match="signaling NaN") as caught:
            _convert(batch)
        for detail in ("decimal-source", "batch 7", repr(column), "row index 42", "sNaN"):
            assert detail in str(caught.value)
        # Comparing signaling NaN itself can signal; check its identity instead.
        assert batch.data[column].iloc[0] is value
        assert batch.data[column].dtype == before[column].dtype
        assert batch.data.index.equals(before.index)
        pd.testing.assert_frame_equal(batch.data.drop(columns=column), before.drop(columns=column))

    @pytest.mark.parametrize("column", TARGET_COLUMNS)
    def test_quiet_decimal_nan_keeps_existing_null_semantics(self, column):
        batch = _single_batch()
        value = Decimal("NaN")
        batch.data[column] = pd.Series([value], dtype=object)
        out = _convert(batch)
        assert pd.isna(out.data[column].iloc[0])
        assert batch.data[column].iloc[0] is value


# ─── Quantity Conversion ────────────────────────────────────────────


class TestQuantityConversion:
    @pytest.mark.parametrize(
        "value", ["9007199254740993", "9223372036854775807", "-9223372036854775808"]
    )
    def test_integer_strings_preserve_exact_value(self, value):
        out = _convert(_single_batch(Quantity=value))
        assert out.data["quantity"].iloc[0] == int(value)

    @pytest.mark.parametrize("value", ["1.0000000000000001", "9007199254740993.1"])
    def test_fractional_strings_are_not_rounded_to_integers(self, value):
        with pytest.raises(TypeConversionError, match="fractional"):
            _convert(_single_batch(Quantity=value))

    @pytest.mark.parametrize(
        "value",
        [2**63, -(2**63) - 1, str(2**63), Decimal("1E100"), float(2**63)],
    )
    def test_int64_overflow_has_row_context(self, value):
        batch = _source_batch(
            {key: [val] for key, val in _good_row(Quantity=value).items()}, index=[42]
        )
        before = batch.data.copy(deep=True)
        with pytest.raises(TypeConversionError) as caught:
            _convert(batch)
        message = str(caught.value)
        for detail in ("csv:test.csv", "batch 0", "'quantity'", "row index 42", repr(value)):
            assert detail in message
        pd.testing.assert_frame_equal(batch.data, before)

    def test_int64_bounds_can_coexist_without_float_coercion(self):
        rows = [_good_row(Quantity=value) for value in [str(-(2**63)), str(2**63 - 1), None]]
        batch = _source_batch({key: [row[key] for row in rows] for key in rows[0]})
        values = _convert(batch).data["quantity"]
        assert values.iloc[0] == -(2**63)
        assert values.iloc[1] == 2**63 - 1
        assert pd.isna(values.iloc[2])

    def test_positive_integer_preserved(self):
        out = _convert(_single_batch(Quantity=12))
        assert out.data["quantity"].iloc[0] == 12

    def test_zero_quantity(self):
        out = _convert(_single_batch(Quantity=0))
        assert out.data["quantity"].iloc[0] == 0

    def test_negative_integer_preserved(self):
        out = _convert(_single_batch(Quantity=-96))
        assert out.data["quantity"].iloc[0] == -96

    def test_float_integral_quantity_accepted(self):
        out = _convert(_single_batch(Quantity=12.0))
        assert out.data["quantity"].iloc[0] == 12

    def test_null_quantity_stays_null(self):
        out = _convert(_single_batch(Quantity=None))
        assert pd.isna(out.data["quantity"].iloc[0])

    def test_fractional_quantity_raises(self):
        with pytest.raises(TypeConversionError, match="fractional"):
            _convert(_single_batch(Quantity=12.5))

    def test_nan_quantity_raises_not_silently_null(self):
        """NaN for quantity stays null, not error (it's a null sentinel)."""
        out = _convert(_single_batch(Quantity=float("nan")))
        assert pd.isna(out.data["quantity"].iloc[0])

    def test_inf_quantity_raises(self):
        with pytest.raises(TypeConversionError, match="non-finite"):
            _convert(_single_batch(Quantity=float("inf")))

    def test_dtype_is_nullable_int64(self):
        out = _convert(_single_batch())
        assert out.data["quantity"].dtype == pd.Int64Dtype()

    def test_string_integer_quantity_accepted(self):
        out = _convert(_single_batch(Quantity="12"))
        assert out.data["quantity"].iloc[0] == 12

    def test_string_non_numeric_quantity_raises(self):
        with pytest.raises(TypeConversionError, match="cannot parse"):
            _convert(_single_batch(Quantity="twelve"))


# ─── Invoice Date Conversion ────────────────────────────────────────


class TestInvoiceDateConversion:
    @pytest.mark.parametrize(
        "value",
        [
            "2500-01-01 00:00:00",
            datetime(1500, 1, 1),
            pd.Timestamp("2500-01-01"),
            np.datetime64("2500-01-01"),
        ],
    )
    def test_out_of_bounds_date_has_row_context_and_cause(self, value):
        batch = _source_batch(
            {key: [val] for key, val in _good_row(InvoiceDate=value).items()}, index=[42]
        )
        before = batch.data.copy(deep=True)
        with pytest.raises(TypeConversionError) as caught:
            _convert(batch)
        for detail in ("csv:test.csv", "batch 0", "'invoice_date'", "row index 42"):
            assert detail in str(caught.value)
        assert "1500" in str(caught.value) or "2500" in str(caught.value)
        assert caught.value.__cause__ is not None
        pd.testing.assert_frame_equal(batch.data, before)

    def test_timezone_aware_input_is_not_silently_normalized(self):
        value = datetime(2020, 1, 1, tzinfo=timezone.utc)
        with pytest.raises(TypeConversionError, match="timezone"):
            _convert(_single_batch(InvoiceDate=value))

    def test_date_parse_error_does_not_duplicate_unbounded_bad_value(self):
        with pytest.raises(TypeConversionError) as caught:
            _convert(_single_batch(InvoiceDate="x" * 10_000))
        assert len(str(caught.value)) < 500
        assert caught.value.__cause__ is not None

    def test_string_date_parsed_correctly(self):
        out = _convert(_single_batch(InvoiceDate="2009-12-01 07:45:00"))
        result = out.data["invoice_date"].iloc[0]
        assert result == pd.Timestamp("2009-12-01 07:45:00")

    def test_native_timestamp_passed_through(self):
        ts = pd.Timestamp("2009-12-01 07:45:00")
        out = _convert(_single_batch(InvoiceDate=ts))
        assert out.data["invoice_date"].iloc[0] == ts

    def test_native_datetime_accepted(self):
        dt = datetime(2009, 12, 1, 7, 45, 0)
        out = _convert(_single_batch(InvoiceDate=dt))
        assert out.data["invoice_date"].iloc[0] == pd.Timestamp(dt)

    def test_numpy_datetime64_accepted(self):
        nd = np.datetime64("2009-12-01T07:45:00")
        out = _convert(_single_batch(InvoiceDate=nd))
        assert out.data["invoice_date"].iloc[0] == pd.Timestamp("2009-12-01 07:45:00")

    def test_null_date_stays_nat(self):
        out = _convert(_single_batch(InvoiceDate=None))
        assert pd.isna(out.data["invoice_date"].iloc[0])

    def test_ambiguous_date_format_raises(self):
        """MM/DD/YYYY is not the supported format."""
        with pytest.raises(TypeConversionError, match="cannot parse date"):
            _convert(_single_batch(InvoiceDate="12/01/2009 07:45:00"))

    def test_invalid_date_string_raises(self):
        with pytest.raises(TypeConversionError, match="cannot parse date"):
            _convert(_single_batch(InvoiceDate="not-a-date"))

    def test_integer_date_raises(self):
        with pytest.raises(TypeConversionError, match="unsupported type"):
            _convert(_single_batch(InvoiceDate=20091201))

    def test_dtype_is_datetime64_ns(self):
        out = _convert(_single_batch())
        assert out.data["invoice_date"].dtype == np.dtype("datetime64[ns]")

    def test_no_timezone_assigned(self):
        out = _convert(_single_batch(InvoiceDate="2009-12-01 07:45:00"))
        assert out.data["invoice_date"].dt.tz is None


# ─── Unit Price Conversion ──────────────────────────────────────────


class TestUnitPriceConversion:
    @pytest.mark.parametrize("factory", [float, np.float32, np.float64])
    @pytest.mark.parametrize("as_object", [False, True])
    @pytest.mark.parametrize("text", ["2.95", "-2.95", "1.2345"])
    def test_floats_use_original_dtype_round_trip_text(self, factory, as_object, text):
        value = factory(text)
        batch = _single_batch(Price=value)
        if as_object:
            batch.data["unit_price"] = pd.Series([value], dtype=object)
        before = batch.data.copy(deep=True)
        out = _convert(batch)
        assert out.data["unit_price"].iloc[0] == Decimal(text)
        pd.testing.assert_frame_equal(batch.data, before)

    @pytest.mark.parametrize(
        "value",
        [
            np.float32("1.23456"),
            np.float64("1.23456"),
            np.nextafter(np.float32("2.95"), np.float32("inf")),
            np.nextafter(np.float64("2.95"), np.float64("inf")),
            0.1 + 0.2,
        ],
    )
    def test_meaningful_float_digits_are_not_rounded(self, value):
        with pytest.raises(TypeConversionError, match="decimal places"):
            _convert(_single_batch(Price=value))

    @pytest.mark.parametrize("factory", [np.float32, np.float64])
    @pytest.mark.parametrize("text", ["inf", "-inf"])
    def test_numpy_infinite_prices_are_rejected(self, factory, text):
        with pytest.raises(TypeConversionError, match="non-finite"):
            _convert(_single_batch(Price=factory(text)))

    @pytest.mark.parametrize("factory", [np.float32, np.float64])
    def test_numpy_nan_prices_remain_null(self, factory):
        out = _convert(_single_batch(Price=factory("nan")))
        assert pd.isna(out.data["unit_price"].iloc[0])

    @pytest.mark.parametrize(
        "value, expected",
        [
            # At this magnitude float32's shortest round-trip text is 9.999999e7;
            # decimal intent follows that text, rather than its exact binary integer.
            (np.float32("99999992"), Decimal("99999990")),
            (np.float64("99999999.9999"), Decimal("99999999.9999")),
            (np.float64("-99999999.9999"), Decimal("-99999999.9999")),
        ],
    )
    def test_numpy_prices_at_supported_precision(self, value, expected):
        assert _convert(_single_batch(Price=value)).data["unit_price"].iloc[0] == expected

    @pytest.mark.parametrize("factory", [np.float32, np.float64])
    def test_numpy_prices_beyond_integral_precision_are_rejected(self, factory):
        # float32 cannot retain the fractional digits at this magnitude: its
        # already-rounded input is 100000000, which must not be repaired here.
        with pytest.raises(TypeConversionError, match="exceeds max_digits"):
            _convert(_single_batch(Price=factory("100000000")))

    @pytest.mark.parametrize(
        "text", ["10.0000", "1.2300", "-1.2300", "-0.0000", "99999999.9999", "0E+100"]
    )
    def test_valid_decimal_representation_is_preserved(self, text):
        value = Decimal(text)
        result = _convert(_single_batch(Price=value)).data["unit_price"].iloc[0]
        assert result is value
        assert result.as_tuple() == value.as_tuple()

    @pytest.mark.parametrize(
        "text, expected",
        [("1.23000", "1.2300"), ("10.000000", "10.0000"), ("-0.000000", "-0.0000")],
    )
    def test_only_zeros_needed_for_scale_compatibility_are_removed(self, text, expected):
        result = _convert(_single_batch(Price=Decimal(text))).data["unit_price"].iloc[0]
        assert result.as_tuple() == Decimal(expected).as_tuple()

    @pytest.mark.parametrize("factory", [str, Decimal])
    @pytest.mark.parametrize(
        "value", ["1.23000", "99999999.9999000", "-1.23000", "0.000000", "0E+100"]
    )
    def test_redundant_decimal_zeros_do_not_require_rounding(self, value, factory):
        out = _convert(_single_batch(Price=factory(value)))
        result = out.data["unit_price"].iloc[0]
        assert result == Decimal(value)
        assert result.as_tuple().exponent >= -4

    def test_scale_check_does_not_round_under_decimal_context(self):
        from decimal import localcontext

        with localcontext() as context:
            context.prec = 6
            with pytest.raises(TypeConversionError, match="decimal places"):
                _convert(_single_batch(Price=Decimal("1.23450000000000000000000000001")))

    def test_positive_float_to_exact_decimal(self):
        out = _convert(_single_batch(Price=6.75))
        result = out.data["unit_price"].iloc[0]
        assert isinstance(result, Decimal)
        assert result == Decimal("6.75")

    def test_zero_price(self):
        out = _convert(_single_batch(Price=0.0))
        assert out.data["unit_price"].iloc[0] == Decimal("0.0")

    def test_negative_price_preserved(self):
        """Negative prices are preserved; business validation handles them."""
        out = _convert(_single_batch(Price=-53594.36))
        result = out.data["unit_price"].iloc[0]
        assert isinstance(result, Decimal)
        assert result == Decimal("-53594.36")

    def test_integer_price_to_decimal(self):
        out = _convert(_single_batch(Price=18))
        result = out.data["unit_price"].iloc[0]
        assert isinstance(result, Decimal)
        assert result == Decimal("18")

    def test_string_price_to_decimal(self):
        out = _convert(_single_batch(Price="2.95"))
        result = out.data["unit_price"].iloc[0]
        assert isinstance(result, Decimal)
        assert result == Decimal("2.95")

    def test_decimal_input_passed_through(self):
        out = _convert(_single_batch(Price=Decimal("2.95")))
        result = out.data["unit_price"].iloc[0]
        assert isinstance(result, Decimal)
        assert result == Decimal("2.95")

    def test_null_price_stays_null(self):
        out = _convert(_single_batch(Price=None))
        assert pd.isna(out.data["unit_price"].iloc[0])

    def test_nan_price_stays_null(self):
        out = _convert(_single_batch(Price=float("nan")))
        assert pd.isna(out.data["unit_price"].iloc[0])

    def test_inf_price_raises(self):
        with pytest.raises(TypeConversionError, match="non-finite"):
            _convert(_single_batch(Price=float("inf")))

    def test_excessive_decimal_places_raises(self):
        with pytest.raises(TypeConversionError, match="decimal places"):
            _convert(_single_batch(Price=Decimal("1.23456")))

    def test_overflow_raises(self):
        with pytest.raises(TypeConversionError, match="exceeds max_digits"):
            _convert(_single_batch(Price=Decimal("999999999.9999")))

    def test_four_decimal_places_accepted(self):
        out = _convert(_single_batch(Price=Decimal("1.2345")))
        assert out.data["unit_price"].iloc[0] == Decimal("1.2345")

    def test_max_valid_value_accepted(self):
        out = _convert(_single_batch(Price=Decimal("99999999.9999")))
        assert out.data["unit_price"].iloc[0] == Decimal("99999999.9999")

    def test_invalid_string_price_raises(self):
        with pytest.raises(TypeConversionError, match="cannot parse"):
            _convert(_single_batch(Price="free"))

    def test_result_dtype_is_object_containing_decimals(self):
        out = _convert(_single_batch())
        assert out.data["unit_price"].dtype == object
        assert isinstance(out.data["unit_price"].iloc[0], Decimal)


# ─── Batch-Level Behavior ───────────────────────────────────────────


class TestBatchBehavior:
    def test_row_count_preserved(self):
        rows = [_good_row(Invoice=f"INV{i}") for i in range(5)]
        batch = _source_batch({k: [r[k] for r in rows] for k in rows[0]})
        out = _convert(batch)
        assert len(out) == 5

    def test_row_order_preserved(self):
        invoices = ["Z999", "A001", "M500"]
        rows = [_good_row(Invoice=inv) for inv in invoices]
        batch = _source_batch({k: [r[k] for r in rows] for k in rows[0]})
        out = _convert(batch)
        assert out.data["invoice_no"].tolist() == invoices

    def test_index_preserved(self):
        rows = [_good_row(Invoice=f"INV{i}") for i in range(3)]
        batch = _source_batch(
            {k: [r[k] for r in rows] for k in rows[0]},
            index=[10, 20, 30],
        )
        out = _convert(batch)
        assert list(out.data.index) == [10, 20, 30]

    def test_source_metadata_preserved(self):
        stamp = datetime(2026, 5, 1, 8, 0, tzinfo=timezone.utc)
        batch = _source_batch(
            {k: [v] for k, v in _good_row().items()},
            source_name="excel:data.xlsx:Sheet1",
            batch_number=42,
            extracted_at=stamp,
        )
        out = _convert(batch)
        assert out.source_name == "excel:data.xlsx:Sheet1"
        assert out.batch_number == 42
        assert out.extracted_at == stamp

    def test_input_dataframe_not_mutated(self):
        batch = _source_batch({k: [v] for k, v in _good_row().items()})
        before = batch.data.copy(deep=True)
        _convert(batch)
        pd.testing.assert_frame_equal(batch.data, before)

    def test_output_columns_are_target_columns_in_order(self):
        out = _convert(_single_batch())
        assert list(out.data.columns) == list(TARGET_COLUMNS)

    def test_empty_batch_handled_consistently(self):
        empty_data = {col: [] for col in CANONICAL_COLUMNS}
        batch = _source_batch(empty_data)
        out = _convert(batch)
        assert len(out) == 0
        assert list(out.data.columns) == list(TARGET_COLUMNS)

    def test_failed_conversion_never_returns_partial_batch(self):
        """If row 2 of 3 is bad, no batch is returned — exception is raised."""
        rows = [
            _good_row(Invoice="INV1", Quantity=10),
            _good_row(Invoice="INV2", Quantity=10.5),  # fractional → fail
            _good_row(Invoice="INV3", Quantity=20),
        ]
        batch = _source_batch({k: [r[k] for r in rows] for k in rows[0]})
        with pytest.raises(TypeConversionError, match="fractional"):
            _convert(batch)

    def test_conversion_is_deterministic(self):
        batch = _source_batch({k: [v] for k, v in _good_row().items()})
        out1 = _convert(batch)
        out2 = _convert(batch)
        pd.testing.assert_frame_equal(out1.data, out2.data)
        assert out1.source_name == out2.source_name
        assert out1.batch_number == out2.batch_number


# ─── Error Reporting ────────────────────────────────────────────────


class TestErrorReporting:
    @pytest.mark.parametrize("column", ["quantity", "invoice_date"])
    @pytest.mark.parametrize("individual_failure", [True, False])
    def test_pandas_array_failure_is_wrapped(self, monkeypatch, column, individual_failure):
        import etl.transforms.type_converter as module

        rows = [_good_row(Quantity=12), _good_row(Quantity=13)]
        batch = _source_batch({key: [row[key] for row in rows] for key in rows[0]}, index=[42, 43])
        dtype = pd.Int64Dtype() if column == "quantity" else "datetime64[ns]"
        original_array = pd.array

        def failing_array(values, *, dtype):
            if dtype == target_dtype and (len(values) > 1 or individual_failure):
                raise ValueError("pandas cast failed")
            return original_array(values, dtype=dtype)

        target_dtype = dtype
        monkeypatch.setattr(module.pd, "array", failing_array)
        with pytest.raises(TypeConversionError) as caught:
            _convert(batch)
        message = str(caught.value)
        for detail in ("csv:test.csv", "batch 0", repr(column), "cannot construct"):
            assert detail in message
        assert isinstance(caught.value.__cause__, ValueError)
        assert str(caught.value.__cause__) == "pandas cast failed"
        if individual_failure:
            assert "row index 42" in message
            assert repr(batch.data[column].tolist()[0]) in message
        else:
            assert "no individual failing row" in message

    def test_error_includes_source_name(self):
        batch = _source_batch(
            {k: [v] for k, v in _good_row(Quantity=12.5).items()},
            source_name="csv:transactions.csv",
        )
        with pytest.raises(TypeConversionError, match="csv:transactions.csv"):
            _convert(batch)

    def test_error_includes_batch_number(self):
        batch = _source_batch(
            {k: [v] for k, v in _good_row(Quantity=12.5).items()},
            batch_number=7,
        )
        with pytest.raises(TypeConversionError, match="batch 7"):
            _convert(batch)

    def test_error_includes_column_name(self):
        with pytest.raises(TypeConversionError, match="'quantity'"):
            _convert(_single_batch(Quantity=12.5))

    def test_error_includes_row_index(self):
        batch = _source_batch(
            {k: [v] for k, v in _good_row(Quantity=12.5).items()},
            index=[42],
        )
        with pytest.raises(TypeConversionError, match="row index 42"):
            _convert(batch)

    def test_error_includes_bad_value(self):
        with pytest.raises(TypeConversionError, match="12.5"):
            _convert(_single_batch(Quantity=12.5))

    def test_error_chains_original_exception(self):
        with pytest.raises(TypeConversionError) as exc_info:
            _convert(_single_batch(InvoiceDate="not-a-date"))
        assert exc_info.value.__cause__ is not None

    def test_error_is_an_etl_error(self):
        from etl.exceptions import ETLError

        with pytest.raises(ETLError):
            _convert(_single_batch(Quantity=12.5))

    def test_error_is_a_dataxai_error(self):
        from core.exceptions import DataXAiError

        with pytest.raises(DataXAiError):
            _convert(_single_batch(Quantity=12.5))


# ─── Integration with AliasMapper via Real Fixtures ─────────────────


class TestIntegrationWithFixtures:
    """Full pipeline: Source → AliasMapper → TypeConverter using real fixture data."""

    def _csv_pipeline(self):
        mapper = AliasMapper()
        converter = TypeConverter()
        with CsvSource(FIXTURES / "online_retail_ii_sample.csv", batch_size=50_000) as src:
            for sb in src.extract():
                mapped = mapper.transform(sb)
                yield converter.convert(mapped)

    def _excel_pipeline(self):
        mapper = AliasMapper()
        converter = TypeConverter()
        with ExcelSource(FIXTURES / "online_retail_ii_sample.xlsx", batch_size=50_000) as src:
            for sb in src.extract():
                mapped = mapper.transform(sb)
                yield converter.convert(mapped)

    def test_csv_fixture_converts_all_18_rows(self):
        all_batches = list(self._csv_pipeline())
        total_rows = sum(len(b) for b in all_batches)
        assert total_rows == 18

    def test_excel_fixture_converts_all_rows(self):
        all_batches = list(self._excel_pipeline())
        total_rows = sum(len(b) for b in all_batches)
        assert total_rows == 18

    def test_csv_output_schema(self):
        for batch in self._csv_pipeline():
            assert list(batch.data.columns) == list(TARGET_COLUMNS)
            assert batch.data["quantity"].dtype == pd.Int64Dtype()
            assert batch.data["invoice_date"].dtype == np.dtype("datetime64[ns]")
            assert batch.data["invoice_no"].dtype == pd.StringDtype()
            assert batch.data["stock_code"].dtype == pd.StringDtype()
            assert batch.data["external_customer_id"].dtype == pd.StringDtype()
            assert batch.data["description"].dtype == pd.StringDtype()
            assert batch.data["country"].dtype == pd.StringDtype()
            assert batch.data["unit_price"].dtype == object
            for val in batch.data["unit_price"]:
                assert isinstance(val, Decimal) or pd.isna(val)

    def test_csv_negative_quantity_preserved(self):
        for batch in self._csv_pipeline():
            neg = batch.data[batch.data["quantity"] < 0]
            if len(neg) > 0:
                for q in neg["quantity"]:
                    assert q < 0

    def test_csv_negative_price_preserved_as_decimal(self):
        for batch in self._csv_pipeline():
            neg = batch.data[
                batch.data["unit_price"].apply(lambda v: isinstance(v, Decimal) and v < 0)
            ]
            if len(neg) > 0:
                for p in neg["unit_price"]:
                    assert isinstance(p, Decimal)
                    assert p < 0

    def test_csv_null_customer_ids_stay_null(self):
        for batch in self._csv_pipeline():
            null_count = batch.data["external_customer_id"].isna().sum()
            assert null_count > 0  # fixture has null customer IDs

    def test_csv_customer_id_13085_not_13085_0(self):
        for batch in self._csv_pipeline():
            ids = batch.data["external_customer_id"].dropna()
            assert "13085" in ids.values
            assert "13085.0" not in ids.values

    def test_csv_and_excel_produce_same_row_count(self):
        csv_total = sum(len(b) for b in self._csv_pipeline())
        xlsx_total = sum(len(b) for b in self._excel_pipeline())
        assert csv_total == xlsx_total == 18

    @requires_postgres
    def test_postgres_converter_matches_csv_fixture_and_preserves_batches(self):
        converted = []
        with PostgresSource(SOURCE_DB_URL, table="raw_transactions", batch_size=5) as source:
            for batch in source.extract():
                mapped = AliasMapper().transform(batch)
                before = mapped.data.copy(deep=True)
                out = _convert(mapped)
                pd.testing.assert_frame_equal(mapped.data, before)
                assert out.data.index.equals(mapped.data.index)
                assert (out.source_name, out.batch_number, out.extracted_at) == (
                    batch.source_name,
                    batch.batch_number,
                    batch.extracted_at,
                )
                converted.append(out)
        assert [len(batch) for batch in converted] == [5, 5, 5, 3]
        actual = pd.concat([batch.data for batch in converted], ignore_index=True)
        expected = pd.concat([batch.data for batch in self._csv_pipeline()], ignore_index=True)
        pd.testing.assert_frame_equal(actual, expected)
