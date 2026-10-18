"""
TypeConverter (Volume 4.3.2): converts alias-mapped columns to their
warehouse-target representations.

Pipeline position: SourceBatch → AliasMapper → TransformedBatch →
TypeConverter → TransformedBatch (same contract, stronger dtype
guarantees).

This module converts types ONLY.  It does not:
- fill, drop, or re-order rows
- mutate the input batch or its DataFrame
- parse ambiguous dates or assign timezones
- round, truncate, or clamp numeric values
- filter administrative stock codes or classify cancellations
- implement validation, healing, anomaly detection, or loading

On any invalid non-null value the converter raises TypeConversionError
with full diagnostic context and does NOT return a partial batch.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import numpy as np
import pandas as pd

from etl.exceptions import TypeConversionError
from etl.transforms.contract import TARGET_COLUMNS, TransformedBatch

# Maximum precision for unit_price, matching OrderLine.unit_price
# DecimalField(max_digits=12, decimal_places=4).
_PRICE_MAX_DIGITS = 12
_PRICE_DECIMAL_PLACES = 4
# Maximum absolute value representable in max_digits=12, decimal_places=4:
# 99999999.9999
_PRICE_MAX_INTEGRAL_DIGITS = _PRICE_MAX_DIGITS - _PRICE_DECIMAL_PLACES

# String columns that receive the nullable-string-ID treatment: strip a
# trailing ".0" from float-like representations of integral IDs without
# converting genuine textual values or turning NaN into "nan".
_ID_COLUMNS = ("invoice_no", "stock_code", "external_customer_id")

# Columns that are simple pass-through strings (no ID cleanup needed).
_PASSTHROUGH_STR_COLUMNS = ("description", "country")

# The single supported date format from the CSV fixture.  Native
# datetime/Timestamp values are also accepted without parsing.
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def _is_null(value, *, source_name: str, batch_number: int, column: str, row_index) -> bool:
    """Retain ordinary nulls/quiet NaN; reject signaling NaN with row context."""
    if isinstance(value, Decimal):
        if value.is_snan():
            raise TypeConversionError(
                source_name=source_name,
                batch_number=batch_number,
                column=column,
                row_index=row_index,
                bad_value=value,
                reason="signaling NaN cannot be converted",
            )
        return value.is_qnan()
    if value is None or value is pd.NaT:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    return False


def _convert_nullable_string_id(
    value, *, column: str, source_name: str, batch_number: int, row_index
) -> object:
    """Convert a single value to a nullable string identifier.

    Rules:
    - null → null (pd.NA)
    - float 13085.0 → "13085" (strip .0 for integral floats)
    - int 13085 → "13085"
    - str "00123" → "00123" (preserve leading zeros)
    - float NaN → pd.NA (not the string "nan")
    """
    if _is_null(
        value,
        source_name=source_name,
        batch_number=batch_number,
        column=column,
        row_index=row_index,
    ):
        return pd.NA

    if isinstance(value, str):
        return value

    if isinstance(value, (int, np.integer)):
        return str(int(value))

    if isinstance(value, (float, np.floating)):
        if not math.isfinite(value):
            raise TypeConversionError(
                source_name=source_name,
                batch_number=batch_number,
                column=column,
                row_index=row_index,
                bad_value=value,
                reason="non-finite float cannot be converted to string ID",
            )
        # Integral float → strip .0
        if value == int(value):
            return str(int(value))
        # Non-integral float — ambiguous
        raise TypeConversionError(
            source_name=source_name,
            batch_number=batch_number,
            column=column,
            row_index=row_index,
            bad_value=value,
            reason="non-integral float cannot be unambiguously converted to string ID",
        )

    # Fallback: convert to string
    return str(value)


def _convert_nullable_string_passthrough(
    value, *, source_name: str, batch_number: int, column: str, row_index
) -> object:
    """Pass through string values; null stays null."""
    if _is_null(
        value,
        source_name=source_name,
        batch_number=batch_number,
        column=column,
        row_index=row_index,
    ):
        return pd.NA
    if isinstance(value, str):
        return value
    return str(value)


def _convert_quantity(value, *, source_name: str, batch_number: int, row_index) -> object:
    """Convert exactly to a nullable Int64; reject fractions and overflow."""
    if _is_null(
        value,
        source_name=source_name,
        batch_number=batch_number,
        column="quantity",
        row_index=row_index,
    ):
        return pd.NA

    # Parse strings without a binary float intermediate (including large integers
    # and fractions too small for float to distinguish from an integer).
    number = value
    if isinstance(value, str):
        try:
            number = Decimal(value)
        except InvalidOperation as exc:
            raise TypeConversionError(
                source_name=source_name,
                batch_number=batch_number,
                column="quantity",
                row_index=row_index,
                bad_value=value,
                reason="cannot parse as numeric quantity",
                cause=exc,
            ) from exc

    if not isinstance(number, (int, np.integer, float, np.floating, Decimal)):
        reason = f"unsupported type {type(value).__name__} for quantity"
    elif (isinstance(number, Decimal) and not number.is_finite()) or (
        isinstance(number, (float, np.floating)) and not math.isfinite(number)
    ):
        reason = "non-finite value cannot represent a quantity"
    elif number < -(2**63) or number >= 2**63:
        reason = "quantity is outside the nullable Int64 range"
    elif number != int(number):
        reason = "fractional quantity cannot be represented as integer without rounding"
    else:
        return int(number)

    raise TypeConversionError(
        source_name=source_name,
        batch_number=batch_number,
        column="quantity",
        row_index=row_index,
        bad_value=value,
        reason=reason,
    )


def _convert_invoice_date(value, *, source_name: str, batch_number: int, row_index) -> object:
    """Convert to datetime64[ns]-compatible value.

    Accepts:
    - Native datetime / Timestamp / datetime64 → pass through
    - String in YYYY-MM-DD HH:MM:SS format → parse strictly
    - null → pd.NaT

    Rejects ambiguous formats, invalid dates, and non-datetime types.
    Does NOT assign or convert timezones.
    """
    if _is_null(
        value,
        source_name=source_name,
        batch_number=batch_number,
        column="invoice_date",
        row_index=row_index,
    ):
        return pd.NaT

    try:
        if isinstance(value, str):
            timestamp = pd.Timestamp(datetime.strptime(value, _DATE_FORMAT))
        elif isinstance(value, (pd.Timestamp, np.datetime64, datetime, date)):
            timestamp = pd.Timestamp(value)
        else:
            raise TypeConversionError(
                source_name=source_name,
                batch_number=batch_number,
                column="invoice_date",
                row_index=row_index,
                bad_value=value,
                reason=f"unsupported type {type(value).__name__} for invoice_date",
            )

        if timestamp.tzinfo is not None:
            raise TypeConversionError(
                source_name=source_name,
                batch_number=batch_number,
                column="invoice_date",
                row_index=row_index,
                bad_value=value,
                reason="timezone-aware date cannot be represented without timezone conversion",
            )
        # Check nanosecond bounds at the original row, before array construction.
        return timestamp.as_unit("ns")
    except (ValueError, TypeError, OverflowError) as exc:
        raise TypeConversionError(
            source_name=source_name,
            batch_number=batch_number,
            column="invoice_date",
            row_index=row_index,
            bad_value=value,
            reason=f"cannot parse date as datetime64[ns]; expected {_DATE_FORMAT!r}",
            cause=exc,
        ) from exc


def _convert_unit_price(value, *, source_name: str, batch_number: int, row_index) -> object:
    """Convert to exact Decimal matching OrderLine.unit_price precision.

    Python floats use their shortest round-trip str(); NumPy floats use
    NumPy's shortest round-trip representation for their original dtype,
    without first widening to Python float. This defines decimal intent
    for floating inputs; it does not recover a pre-existing source value.
    Meaningful digits beyond max_digits=12, decimal_places=4 are rejected
    without rounding. Already-compatible Decimal representations are retained.

    Negative prices are preserved at this stage (business validation
    handles their status later).
    """
    if _is_null(
        value,
        source_name=source_name,
        batch_number=batch_number,
        column="unit_price",
        row_index=row_index,
    ):
        return pd.NA

    try:
        if isinstance(value, Decimal):
            d = value
        elif isinstance(value, (float, np.floating)):
            if not np.isfinite(value):
                raise TypeConversionError(
                    source_name=source_name,
                    batch_number=batch_number,
                    column="unit_price",
                    row_index=row_index,
                    bad_value=value,
                    reason="non-finite float cannot represent a price",
                )
            decimal_text = (
                np.format_float_scientific(value, unique=True, trim="-")
                if isinstance(value, np.floating)
                else str(value)
            )
            d = Decimal(decimal_text)
        elif isinstance(value, (int, np.integer)):
            d = Decimal(int(value))
        elif isinstance(value, str):
            d = Decimal(value)
        else:
            raise TypeConversionError(
                source_name=source_name,
                batch_number=batch_number,
                column="unit_price",
                row_index=row_index,
                bad_value=value,
                reason=f"unsupported type {type(value).__name__} for unit_price",
            )
    except InvalidOperation as exc:
        raise TypeConversionError(
            source_name=source_name,
            batch_number=batch_number,
            column="unit_price",
            row_index=row_index,
            bad_value=value,
            reason="cannot parse as Decimal",
            cause=exc,
        ) from exc

    if not d.is_finite():
        raise TypeConversionError(
            source_name=source_name,
            batch_number=batch_number,
            column="unit_price",
            row_index=row_index,
            bad_value=value,
            reason="non-finite Decimal cannot represent a price",
        )

    # Remove only the zeros needed to reach the supported scale. Do not use
    # Decimal.normalize(), which applies context rounding and changes valid input.
    sign, digits, exponent = d.as_tuple()
    if d.is_zero():
        return (
            d
            if exponent >= -_PRICE_DECIMAL_PLACES
            else Decimal((sign, (0,), -_PRICE_DECIMAL_PLACES))
        )
    adjusted = exponent < -_PRICE_DECIMAL_PLACES
    while exponent < -_PRICE_DECIMAL_PLACES and digits[-1] == 0:
        digits = digits[:-1]
        exponent += 1

    if exponent < -_PRICE_DECIMAL_PLACES:
        raise TypeConversionError(
            source_name=source_name,
            batch_number=batch_number,
            column="unit_price",
            row_index=row_index,
            bad_value=value,
            reason=(
                f"value has more than {_PRICE_DECIMAL_PLACES} decimal places "
                f"and would require rounding"
            ),
        )

    # Count integral digits: total significant digits minus decimal places.
    if exponent >= 0:
        integral_digits = len(digits) + exponent
    else:
        integral_digits = max(0, len(digits) + exponent)

    if integral_digits > _PRICE_MAX_INTEGRAL_DIGITS:
        raise TypeConversionError(
            source_name=source_name,
            batch_number=batch_number,
            column="unit_price",
            row_index=row_index,
            bad_value=value,
            reason=(
                f"value exceeds max_digits={_PRICE_MAX_DIGITS} "
                f"with decimal_places={_PRICE_DECIMAL_PLACES}"
            ),
        )

    return Decimal((sign, digits, exponent)) if adjusted else d


def _build_array(values, dtype, *, original, source_name: str, batch_number: int, column: str):
    """Wrap pandas cast errors, locating the original failing row when possible."""
    try:
        return pd.array(values, dtype=dtype)
    except (TypeError, ValueError, OverflowError) as exc:
        for idx, raw, converted in zip(original.index, original, values, strict=True):
            try:
                pd.array([converted], dtype=dtype)
            except (TypeError, ValueError, OverflowError) as row_exc:
                raise TypeConversionError(
                    source_name=source_name,
                    batch_number=batch_number,
                    column=column,
                    row_index=idx,
                    bad_value=raw,
                    reason=f"cannot construct {dtype} array ({type(row_exc).__name__})",
                    cause=row_exc,
                ) from row_exc
        # A combination-level cast failure may have no single offending row.
        raise TypeConversionError(
            source_name=source_name,
            batch_number=batch_number,
            column=column,
            row_index=None,
            bad_value=None,
            reason=f"cannot construct {dtype} array; no individual failing row",
            cause=exc,
        ) from exc


class TypeConverter:
    """Converts alias-mapped TransformedBatch columns to warehouse types.

    Pipeline: SourceBatch → AliasMapper → TransformedBatch →
    TypeConverter → TransformedBatch (same shape, typed columns).

    Does not mutate the input.  On any invalid non-null value, raises
    TypeConversionError and does NOT return a partial batch.
    """

    def convert(self, batch: TransformedBatch) -> TransformedBatch:
        """Type-convert every column in *batch* and return a new
        TransformedBatch with the same metadata.

        The input DataFrame is never modified.
        """
        source_name = batch.source_name
        batch_number = batch.batch_number

        # Work on a copy so we never touch the input.
        df = batch.data.copy()

        # --- String ID columns ---
        for col in _ID_COLUMNS:
            converted = []
            for idx, value in zip(df.index, df[col], strict=True):
                converted.append(
                    _convert_nullable_string_id(
                        value,
                        column=col,
                        source_name=source_name,
                        batch_number=batch_number,
                        row_index=idx,
                    )
                )
            df[col] = pd.array(converted, dtype=pd.StringDtype())

        # --- Passthrough string columns ---
        for col in _PASSTHROUGH_STR_COLUMNS:
            converted = []
            for idx, value in zip(df.index, df[col], strict=True):
                converted.append(
                    _convert_nullable_string_passthrough(
                        value,
                        source_name=source_name,
                        batch_number=batch_number,
                        column=col,
                        row_index=idx,
                    )
                )
            df[col] = pd.array(converted, dtype=pd.StringDtype())

        # --- Quantity → nullable integer ---
        qty_converted = []
        for idx, value in zip(df.index, df["quantity"], strict=True):
            qty_converted.append(
                _convert_quantity(
                    value,
                    source_name=source_name,
                    batch_number=batch_number,
                    row_index=idx,
                )
            )
        df["quantity"] = _build_array(
            qty_converted,
            pd.Int64Dtype(),
            original=batch.data["quantity"],
            source_name=source_name,
            batch_number=batch_number,
            column="quantity",
        )

        # --- Invoice date → datetime64[ns] ---
        date_converted = []
        for idx, value in zip(df.index, df["invoice_date"], strict=True):
            date_converted.append(
                _convert_invoice_date(
                    value,
                    source_name=source_name,
                    batch_number=batch_number,
                    row_index=idx,
                )
            )
        df["invoice_date"] = _build_array(
            date_converted,
            "datetime64[ns]",
            original=batch.data["invoice_date"],
            source_name=source_name,
            batch_number=batch_number,
            column="invoice_date",
        )

        # --- Unit price → Decimal (object column) ---
        price_converted = []
        # Series iteration can widen float32 to Python float; its array retains dtype.
        for idx, value in zip(df.index, df["unit_price"].array, strict=True):
            price_converted.append(
                _convert_unit_price(
                    value,
                    source_name=source_name,
                    batch_number=batch_number,
                    row_index=idx,
                )
            )
        # Store as object column — pandas does not have a native Decimal dtype
        df["unit_price"] = pd.array(price_converted, dtype=object)

        # Ensure column order matches TARGET_COLUMNS
        df = df[list(TARGET_COLUMNS)]

        return TransformedBatch(
            data=df,
            source_name=source_name,
            batch_number=batch_number,
            extracted_at=batch.extracted_at,
        )
