"""SER-v1 representation, schema validation and actual source-fixture regressions.

Edge cases below are synthetic. Only the CSV/Excel/PostgreSQL integration cases
use the existing 18-row UCI fixture. No source data or schema is written.
"""

import copy
import hashlib
import json
import struct
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone, tzinfo
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest
import pytz

from etl.exceptions import SerializationError
from etl.serialization import decode_source_batch, encode_source_batch, fingerprint_source_batch
from etl.sources import CANONICAL_COLUMNS, CsvSource, ExcelSource, PostgresSource, SourceBatch
from etl.tests.test_sources_postgres import SOURCE_DB_URL, requires_postgres

FIXTURES = Path(__file__).parent / "fixtures"
EXTRACTED = datetime(2026, 10, 23, 10, 11, 12, 123456, tzinfo=timezone.utc)


def _objects(values):
    array = np.empty(len(values), dtype=object)
    for i, value in enumerate(values):
        array[i] = value
    return pd.Series(array, dtype=object)


def _batch(values=("sample",), dtype=object, index=None):
    """All fixtures retain the canonical source schema, plus one test column."""
    series = _objects(values) if dtype is object else pd.Series(values, dtype=dtype)
    frame = pd.DataFrame({name: ["raw"] * len(series) for name in CANONICAL_COLUMNS})
    frame["extra"] = series
    if index is not None:
        frame.index = index
    return SourceBatch(frame, "synthetic:SER-v1", 7, EXTRACTED)


def _roundtrip(batch):
    before = batch.data.copy(deep=True)
    payload = encode_source_batch(batch)
    wire = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    restored = decode_source_batch(json.loads(wire))
    pd.testing.assert_frame_equal(
        restored.data, before, check_exact=True, check_index_type=True, check_column_type=True
    )
    pd.testing.assert_frame_equal(batch.data, before, check_exact=True)
    assert restored.data is not batch.data
    assert restored.source_name == batch.source_name
    assert restored.batch_number == batch.batch_number
    assert restored.extracted_at == batch.extracted_at
    assert type(restored.extracted_at) is type(batch.extracted_at)
    assert encode_source_batch(restored) == payload
    return restored


@pytest.mark.parametrize(
    "value",
    [
        "000123",
        "اردو / کراچی",
        "e\u0301",
        "é",
        "\x00\n\t",
        "",
        True,
        False,
        0,
        -(2**100),
        2**100,
        None,
        pd.NA,
        pd.NaT,
        date(2026, 10, 23),
        datetime(2026, 10, 23, 1, 2, 3, 456789),
    ],
)
def test_python_scalar_types_and_exact_values(value):
    out = _roundtrip(_batch([value])).data["extra"].iloc[0]
    assert type(out) is type(value)
    if value is pd.NA or value is pd.NaT or value is None:
        assert out is value
    else:
        assert out == value


@pytest.mark.parametrize(
    "bits",
    [
        "0000000000000000",
        "8000000000000000",
        "0000000000000001",
        "7fefffffffffffff",
        "7ff0000000000000",
        "fff0000000000000",
        "7ff8000000000042",
        "fff8000000000042",
        "7ff0000000000042",
    ],
)
def test_python_float_bits_including_nan_payloads(bits):
    value = struct.unpack(">d", bytes.fromhex(bits))[0]
    out = _roundtrip(_batch([value])).data["extra"].iloc[0]
    assert type(out) is float
    assert struct.pack(">d", out).hex() == bits


@pytest.mark.parametrize(
    "dtype",
    [
        np.int8,
        np.int16,
        np.int32,
        np.int64,
        np.uint8,
        np.uint16,
        np.uint32,
        np.uint64,
        np.bool_,
        np.float16,
        np.float32,
        np.float64,
    ],
)
def test_numpy_scalar_runtime_types_are_preserved_in_object_columns(dtype):
    value = dtype(1)
    out = _roundtrip(_batch([value])).data["extra"].iloc[0]
    assert type(out) is dtype
    assert out.tobytes() == value.tobytes()


@pytest.mark.parametrize("dtype", [np.intc, np.uintc])
@pytest.mark.parametrize("object_column", [True, False])
def test_platform_c_integer_runtime_type_is_not_collapsed_to_same_width_alias(dtype, object_column):
    batch = _batch([dtype(42)], dtype=object if object_column else dtype)
    out = _roundtrip(batch).data["extra"].array[0]
    assert type(out) is dtype
    if not object_column:
        assert decode_source_batch(encode_source_batch(batch)).data["extra"].dtype.type is dtype


@pytest.mark.parametrize("object_column", [True, False])
def test_longdouble_is_rejected_even_when_platform_reports_float64_width(object_column):
    batch = _batch([np.longdouble(1)], dtype=object if object_column else np.longdouble)
    with pytest.raises(SerializationError):
        encode_source_batch(batch)


@pytest.mark.parametrize(
    "dtype,bits",
    [
        ("float16", "8000"),
        ("float16", "7e42"),
        ("float32", "80000000"),
        ("float32", "7fc00042"),
        ("float64", "fff8000000000042"),
    ],
)
@pytest.mark.parametrize("object_column", [False, True])
def test_numpy_float_bits_with_and_without_object_dtype(dtype, bits, object_column):
    value = np.frombuffer(bytes.fromhex(bits), dtype=np.dtype(dtype).newbyteorder(">"))[0]
    batch = _batch([value], dtype=object if object_column else dtype)
    out = _roundtrip(batch).data["extra"].array[0]
    assert type(out) is type(value)
    assert np.asarray(out, dtype=np.dtype(dtype).newbyteorder(">")).tobytes().hex() == bits


@pytest.mark.parametrize(
    "text",
    [
        "2.9500",
        "-0.0000",
        "0E+8",
        "1E+30",
        "-12.3456",
        "NaN123",
        "-NaN42",
        "sNaN456",
        "-sNaN",
        "Infinity",
        "-Infinity",
    ],
)
def test_decimal_representation_including_signaling_nan(text):
    value = Decimal(text)
    batch = _batch([value])
    # pandas equality calls Decimal comparisons, which are undefined for sNaN.
    payload = encode_source_batch(batch)
    out = decode_source_batch(json.loads(json.dumps(payload))).data["extra"].iloc[0]
    assert type(out) is Decimal
    assert out.as_tuple() == value.as_tuple()
    assert batch.data["extra"].iloc[0].as_tuple() == value.as_tuple()
    assert encode_source_batch(decode_source_batch(payload)) == payload


@pytest.mark.parametrize("unit", ["Y", "M", "W", "D", "h", "m", "s", "ms", "us", "ns"])
@pytest.mark.parametrize("ticks", [123456, -(2**63)])
def test_numpy_datetime_units_and_typed_nat(unit, ticks):
    value = np.datetime64(ticks, unit)
    out = _roundtrip(_batch([value])).data["extra"].iloc[0]
    assert type(out) is np.datetime64
    assert out.dtype == value.dtype
    assert out.view("i8") == ticks


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 10, 23, tzinfo=timezone.utc),
        datetime(
            2026, 10, 23, tzinfo=timezone(timedelta(hours=5, seconds=3, microseconds=4), "custom")
        ),
        datetime(2026, 10, 23, tzinfo=timezone(-timedelta(hours=7))),
        datetime(2026, 10, 23, tzinfo=ZoneInfo("Asia/Karachi")),
        datetime(2026, 11, 1, 1, 30, tzinfo=ZoneInfo("America/New_York"), fold=0),
        datetime(2026, 11, 1, 1, 30, tzinfo=ZoneInfo("America/New_York"), fold=1),
        datetime(2026, 3, 8, 2, 30, tzinfo=ZoneInfo("America/New_York")),
        datetime(2026, 1, 1, fold=1),
        pytz.timezone("America/New_York").localize(datetime(2026, 11, 1, 1, 30), is_dst=False),
        pytz.UTC.localize(datetime(2026, 1, 1)),
        pd.Timestamp("2026-10-23T14:30:00.123456789"),
        pd.Timestamp("2026-10-23T14:30:00.123456789+05:00"),
        pd.Timestamp("2026-10-23T14:30:00.123456789", tz="Asia/Karachi"),
        pd.Timestamp(datetime(2026, 11, 1, 1, 30, tzinfo=ZoneInfo("America/New_York"), fold=1)),
        pd.Timestamp("1500-01-01").as_unit("s"),
    ],
)
def test_datetime_precision_zone_offset_fold_and_no_naive_timezone_inference(value):
    out = _roundtrip(_batch([value])).data["extra"].iloc[0]
    assert type(out) is type(value)
    assert out.fold == value.fold
    assert out.utcoffset() == value.utcoffset()
    assert type(out.tzinfo) is type(value.tzinfo)
    assert str(out.tzinfo) == str(value.tzinfo)
    if isinstance(value, pd.Timestamp):
        assert out.asm8.dtype == value.asm8.dtype
        assert out.asm8.view("i8") == value.asm8.view("i8")


@pytest.mark.parametrize(
    "dtype,values",
    [
        ("bool", [True, False]),
        ("int8", [-128, 127]),
        ("int16", [-32768, 32767]),
        ("int32", [-(2**31), 2**31 - 1]),
        ("int64", [-(2**63), 2**63 - 1]),
        ("uint8", [0, 255]),
        ("uint16", [0, 65535]),
        ("uint32", [0, 2**32 - 1]),
        ("uint64", [0, 2**64 - 1]),
        ("float16", [-0.0, np.inf]),
        ("float32", [np.nan, -2.95]),
        ("float64", [np.nan, -np.inf]),
        ("Int64", [-(2**63), pd.NA]),
        ("Float64", [-0.0, pd.NA]),
        ("boolean", [True, pd.NA]),
        (pd.StringDtype(storage="python"), ["001", pd.NA]),
        ("datetime64[s]", [pd.Timestamp("2026-10-23"), pd.NaT]),
        ("datetime64[ms]", [pd.Timestamp("2026-10-23T01:02:03.456"), pd.NaT]),
        ("datetime64[us]", [pd.Timestamp("2026-10-23T01:02:03.456789"), pd.NaT]),
        ("datetime64[ns]", [pd.Timestamp("2026-10-23T01:02:03.456789012"), pd.NaT]),
        (
            pd.DatetimeTZDtype(tz="Asia/Karachi"),
            [pd.Timestamp("2026-01-01", tz="Asia/Karachi"), pd.NaT],
        ),
        (
            pd.DatetimeTZDtype(tz=ZoneInfo("America/New_York")),
            [
                pd.Timestamp(
                    datetime(2026, 11, 1, 1, 30, tzinfo=ZoneInfo("America/New_York"), fold=1)
                ),
                pd.NaT,
            ],
        ),
    ],
)
def test_supported_column_dtypes(dtype, values):
    _roundtrip(_batch(values, dtype=dtype))
    _roundtrip(_batch([], dtype=dtype))


@pytest.mark.parametrize(
    "index",
    [
        pd.RangeIndex(10, 16, 2, name="source row"),
        pd.RangeIndex(7, 1, -2),
        pd.Index(["same", "same", "last"], name="duplicate"),
        pd.Index([3, -1, 3], dtype="int32"),
        pd.Index([1.5, -0.0, np.nan]),
        pd.Index([np.int8(2), "two", None], dtype=object),
        pd.Index([True, False, True]),
        pd.Index([1, pd.NA, 1], dtype="Int64"),
        pd.Index(["a", pd.NA, "a"], dtype="string"),
        pd.DatetimeIndex(["2026-01-01", "2026-01-01", None], name="when"),
        pd.DatetimeIndex(["2026-01-01", "2026-01-02", None], tz="Asia/Karachi"),
    ],
)
def test_index_structure_and_duplicate_positions(index):
    batch = _batch(["one", "two", "three"], index=index)
    _roundtrip(batch)
    assert [r["row_position"] for r in encode_source_batch(batch)["records"]] == [0, 1, 2]


def test_duplicate_columns_different_dtypes_names_and_row_duplicates():
    batch = _batch(["same", "same"])
    batch.data.insert(1, "Invoice", pd.Series([7, 7], dtype="Int64"), allow_duplicates=True)
    batch.data.columns.name = "source fields"
    batch.data.index = pd.Index(["same", "same"], name="source row")
    out = _roundtrip(batch)
    assert list(out.data.columns).count("Invoice") == 2
    assert str(out.data.iloc[:, 0].dtype) == "object"
    assert str(out.data.iloc[:, 1].dtype) == "Int64"
    assert out.data.duplicated().sum() == 1


def test_empty_range_and_nullable_column_axis_and_flags():
    batch = _batch([], index=pd.RangeIndex(10, 10, 3, name="empty"))
    batch.data.columns = pd.Index(batch.data.columns, dtype="string", name="fields")
    batch.data.flags.allows_duplicate_labels = False
    _roundtrip(batch)


def test_metadata_timestamp_nanoseconds_and_input_output_are_independent():
    batch = replace(_batch(), extracted_at=pd.Timestamp("2026-10-23T01:02:03.123456789Z"))
    payload = encode_source_batch(batch)
    before = copy.deepcopy(payload)
    out = decode_source_batch(payload)
    out.data.iloc[0, 0] = "changed"
    assert batch.data.iloc[0, 0] == "raw"
    assert payload == before
    assert out.extracted_at.nanosecond == 789
    assert out.extracted_at == batch.extracted_at


def test_nullable_float_distinguishes_unmasked_nan_from_masked_na_and_signed_zero():
    nan = np.frombuffer(bytes.fromhex("fff8000000000042"), dtype=">f8")[0]
    batch = _batch([0, 0, 0])
    batch.data["extra"] = pd.arrays.FloatingArray(
        np.array([nan, 0.0, -0.0]), np.array([False, True, False])
    )
    out = _roundtrip(batch).data["extra"].array
    assert isinstance(out[0], np.float64)
    assert out[0].tobytes() == nan.tobytes()
    assert out[1] is pd.NA
    assert out[2].tobytes() == np.float64(-0.0).tobytes()


@pytest.mark.parametrize(
    "dtype,bits", [("float16", "7c42"), ("float32", "7f800042"), ("float64", "7ff0000000000042")]
)
@pytest.mark.parametrize("object_column", [True, False])
def test_signaling_numpy_nan_bits(dtype, bits, object_column):
    array = np.frombuffer(bytes.fromhex(bits), dtype=np.dtype(dtype).newbyteorder(">")).astype(
        dtype
    )
    batch = _batch([array[0]])
    if not object_column:
        batch.data["extra"] = pd.Series(array)
    out = _roundtrip(batch).data["extra"].array[0]
    assert out.tobytes() == array[0].tobytes()


def test_object_index_keeps_datetime_runtime_types_and_decimal_evidence():
    labels = [datetime(2026, 1, 1), np.datetime64("2026-01-01", "D"), Decimal("-0.00")]
    batch = _batch([1, 2, 3], index=pd.Index(labels, dtype=object))
    out = _roundtrip(batch)
    for actual, expected in zip(out.data.index, labels, strict=True):
        assert type(actual) is type(expected)
    assert out.data.index[2].as_tuple() == labels[2].as_tuple()


def test_ser_v1_wire_shape_and_golden_fingerprint():
    payload = encode_source_batch(_batch())
    assert payload["format"] == "dataxai.source-batch"
    assert payload["version"] == 1
    assert payload["columns"] == list(CANONICAL_COLUMNS) + ["extra"]
    assert payload["dtypes"] == [{"kind": "object"}] * 9
    assert payload["index"] == {
        "kind": "range",
        "name": None,
        "start": "0",
        "stop": "1",
        "step": "1",
    }
    assert payload["records"] == [
        {
            "row_position": 0,
            "index_label": {"type": "int", "value": "0"},
            "values": [{"type": "str", "value": "raw"}] * 8 + [{"type": "str", "value": "sample"}],
        }
    ]
    assert (
        fingerprint_source_batch(_batch())
        == "34945b7ea9a48eae6d466d54af358b02611b4aac8807054d5ad375af2f71339d"
    )


@pytest.mark.parametrize("value", [None, [], "json text", 1, True])
def test_public_entry_points_reject_incompatible_contracts(value):
    with pytest.raises(SerializationError):
        encode_source_batch(value)
    with pytest.raises(SerializationError):
        decode_source_batch(value)


def test_fingerprint_canonical_bytes_determinism_and_provenance_exclusion():
    batch = _batch(["اردو", Decimal("-0.00")])
    payload = encode_source_batch(batch)
    payload.pop("extracted_at")
    payload.pop("batch_number")
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    expected = hashlib.sha256(canonical).hexdigest()
    assert fingerprint_source_batch(batch) == expected
    assert fingerprint_source_batch(decode_source_batch(encode_source_batch(batch))) == expected
    assert (
        fingerprint_source_batch(
            replace(batch, batch_number=999, extracted_at=datetime(2000, 1, 1))
        )
        == expected
    )
    assert len(expected) == 64


@pytest.mark.parametrize(
    "left,right",
    [
        (1, True),
        (1, np.int64(1)),
        (1.0, np.float64(1)),
        (0.0, -0.0),
        (Decimal("1.0"), Decimal("1.00")),
        (None, pd.NA),
        (pd.NaT, np.datetime64("NaT", "ns")),
        (datetime(2026, 1, 1), pd.Timestamp("2026-01-01")),
        ("e\u0301", "é"),
    ],
)
def test_fingerprint_distinguishes_runtime_types_and_representation(left, right):
    assert fingerprint_source_batch(_batch([left])) != fingerprint_source_batch(_batch([right]))


@pytest.mark.parametrize(
    "change",
    [
        "source",
        "row_order",
        "column_order",
        "dtype",
        "index",
        "index_name",
        "column_name",
        "extra_column",
        "row_count",
    ],
)
def test_fingerprint_changes_for_content_schema_and_order(change):
    batch = _batch([np.int64(1), np.int64(2)])
    changed = replace(batch, data=batch.data.copy(deep=True))
    if change == "source":
        changed = replace(changed, source_name="other")
    elif change == "row_order":
        changed = replace(changed, data=changed.data.iloc[::-1].reset_index(drop=True))
    elif change == "column_order":
        changed = replace(changed, data=changed.data.iloc[:, ::-1])
    elif change == "dtype":
        changed.data["extra"] = changed.data["extra"].astype("Int64")
    elif change == "index":
        changed.data.index = pd.Index([0, 1], dtype="int64")
    elif change == "index_name":
        changed.data.index.name = "name"
    elif change == "column_name":
        changed.data.columns.name = "name"
    elif change == "extra_column":
        changed.data["more"] = "new"
    else:
        changed = replace(changed, data=changed.data.iloc[:1])
    assert fingerprint_source_batch(batch) != fingerprint_source_batch(changed)


class _CustomZone(tzinfo):
    def utcoffset(self, dt):
        return timedelta(0)


@pytest.mark.parametrize(
    "value",
    [
        [1],
        {"key": 1},
        (1, 2),
        b"bytes",
        object(),
        complex(1, 2),
        timedelta(days=1),
        np.timedelta64(1, "s"),
        np.str_("text"),
        np.datetime64(1, "ps"),
        np.datetime64(1, "2D"),
        datetime(2026, 1, 1, tzinfo=_CustomZone()),
        "\ud800",
    ],
)
def test_unsupported_scalar_rejection_without_mutation(value):
    batch = _batch([value])
    with pytest.raises(SerializationError):
        encode_source_batch(batch)
    assert batch.data["extra"].iloc[0] is value


@pytest.mark.parametrize(
    "dtype,values",
    [
        ("category", ["a"]),
        ("Int32", [1]),
        ("complex128", [1j]),
        ("timedelta64[ns]", [1]),
        ("period[M]", ["2026-01"]),
        (">i4", [1]),
    ],
)
def test_unsupported_dtype_rejection(dtype, values):
    with pytest.raises(SerializationError):
        encode_source_batch(_batch(values, dtype=dtype))


@pytest.mark.parametrize(
    "change",
    [
        "multiindex",
        "categorical_index",
        "frequency",
        "column_label",
        "missing_column",
        "attrs",
        "index_name",
    ],
)
def test_unsupported_structure_and_mutable_batch_schema_checked_at_use(change):
    batch = _batch()
    if change == "multiindex":
        batch.data.index = pd.MultiIndex.from_tuples([("a", 1)])
    elif change == "categorical_index":
        batch.data.index = pd.CategoricalIndex(["a"])
    elif change == "frequency":
        batch.data.index = pd.date_range("2026-01-01", periods=1)
    elif change == "column_label":
        batch.data[42] = 1
    elif change == "missing_column":
        batch.data.drop(columns="Invoice", inplace=True)
    elif change == "attrs":
        batch.data.attrs["custom"] = "value"
    else:
        batch.data.index.name = ("tuple",)
    with pytest.raises(SerializationError):
        encode_source_batch(batch)


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", 2),
        ("version", True),
        ("format", "other"),
        ("row_count", 2),
        ("row_count", -1),
        ("row_count", True),
        ("records", {}),
        ("dtypes", []),
        ("columns", ["Invoice"]),
        ("batch_number", -1),
        ("source_name", 12),
        ("extracted_at", {"type": "none"}),
        ("allows_duplicate_labels", 1),
    ],
)
def test_invalid_top_level_payload(field, value):
    payload = encode_source_batch(_batch())
    payload[field] = value
    with pytest.raises(SerializationError):
        decode_source_batch(payload)


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "extra",
        "row_position",
        "bool_position",
        "index",
        "values",
        "dtype",
        "dtype_cast",
        "index_count",
        "range_step",
    ],
)
def test_malformed_structure_and_incompatible_dtype(change):
    payload = encode_source_batch(_batch([1]))
    if change == "missing":
        del payload["columns"]
    elif change == "extra":
        payload["surprise"] = 1
    elif change == "row_position":
        payload["records"][0]["row_position"] = 1
    elif change == "bool_position":
        payload["records"][0]["row_position"] = False
    elif change == "index":
        payload["records"][0]["index_label"] = {"type": "int", "value": "42"}
    elif change == "values":
        payload["records"][0]["values"].pop()
    elif change == "dtype":
        payload["dtypes"][-1] = {"kind": "numpy", "name": "complex128"}
    elif change == "dtype_cast":
        payload["dtypes"][-1] = {"kind": "numpy", "name": "float64"}
    elif change == "index_count":
        payload["index"]["stop"] = "2"
    else:
        payload["index"]["step"] = "0"
    with pytest.raises(SerializationError):
        decode_source_batch(payload)


@pytest.mark.parametrize(
    "cell",
    [
        {"type": "unknown"},
        {"type": "none", "extra": 0},
        {"type": "bool", "value": 1},
        {"type": "int", "value": "01"},
        {"type": "float", "bits": "nan"},
        {"type": "numpy", "dtype": "bool", "bits": "02"},
        {"type": "decimal", "sign": 0, "digits": [10], "exponent": 0},
        {"type": "decimal", "sign": 0, "digits": [0, 1], "exponent": 0},
        {"type": "decimal", "sign": True, "digits": [1], "exponent": 0},
        {"type": "date", "parts": [2026, 2, 30]},
        {"type": "np_datetime", "unit": "ps", "ticks": "1"},
        {"type": "np_datetime", "unit": "ns", "ticks": str(2**63)},
        {
            "type": "timestamp",
            "unit": "ns",
            "ticks": str(-(2**63)),
            "zone": None,
            "fold": 0,
            "offset": None,
        },
    ],
)
def test_malformed_scalar_tags_values_and_noncanonical_representations(cell):
    payload = encode_source_batch(_batch())
    payload["records"][0]["values"][-1] = cell
    with pytest.raises(SerializationError):
        decode_source_batch(payload)


@pytest.mark.parametrize("change", ["zone", "offset", "fold", "unit"])
def test_timestamp_metadata_tampering(change):
    payload = encode_source_batch(_batch([pd.Timestamp("2026-01-01", tz="Asia/Karachi")]))
    cell = payload["records"][0]["values"][-1]
    if change == "zone":
        cell["zone"]["key"] = "No/Such_Zone"
    elif change == "offset":
        cell["offset"] = 0
    elif change == "fold":
        cell["fold"] = 2
    else:
        cell["unit"] = "D"
    with pytest.raises(SerializationError):
        decode_source_batch(payload)


@pytest.mark.parametrize(
    "source_type,filename",
    [(CsvSource, "online_retail_ii_sample.csv"), (ExcelSource, "online_retail_ii_sample.xlsx")],
)
def test_actual_source_fixture_batches_roundtrip(source_type, filename):
    count = 0
    with source_type(FIXTURES / filename, batch_size=5) as source:
        for batch in source.extract():
            restored = _roundtrip(batch)
            assert fingerprint_source_batch(batch) == fingerprint_source_batch(restored)
            count += len(restored)
    assert count == 18


@requires_postgres
def test_actual_postgres_source_fixture_batches_roundtrip():
    count = 0
    with PostgresSource(SOURCE_DB_URL, table="raw_transactions", batch_size=5) as source:
        for batch in source.extract():
            restored = _roundtrip(batch)
            assert fingerprint_source_batch(batch) == fingerprint_source_batch(restored)
            count += len(restored)
    assert count == 18
