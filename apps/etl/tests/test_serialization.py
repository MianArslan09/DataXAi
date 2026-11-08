"""SER-v1 representation, schema validation and actual source-fixture regressions.

Edge cases below are synthetic. Only the CSV/Excel/PostgreSQL integration cases
use the existing 18-row UCI fixture. No source data or schema is written.
"""

import copy
import hashlib
import json
import os
import struct
import sys
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone, tzinfo
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest
import pytz

from etl import serialization
from etl.exceptions import SerializationError
from etl.serialization import decode_source_batch, encode_source_batch, fingerprint_source_batch
from etl.sources import CANONICAL_COLUMNS, CsvSource, ExcelSource, PostgresSource, SourceBatch
from etl.tests import test_staging_persistence as persistence_tests
from etl.tests import test_staging_reader as reader_tests
from etl.tests.test_sources_postgres import SOURCE_DB_URL, requires_postgres

FIXTURES = Path(__file__).parent / "fixtures"
EXTRACTED = datetime(2026, 10, 23, 10, 11, 12, 123456, tzinfo=timezone.utc)


def _frozen_module(gate, name, pin):
    """Load only independently pinned source, never a mutable reference copy."""
    variable = f"DATAXAI_GATE{gate}_BASELINE"
    root = os.environ.get(variable)
    if root is None:
        pytest.skip(f"Set {variable} to the preserved Gate {gate} checkout")
    path = Path(root) / "apps" / "etl" / f"{name}.py"
    code = path.read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(code).hexdigest() == pin
    module = ModuleType(f"gate{gate}_{name}")
    exec(compile(code, str(path), "exec"), module.__dict__)
    return module


@pytest.fixture(scope="module")
def gate3_codec():
    return _frozen_module(
        3, "serialization", "a00cb039644dc4720548a7c7ea31e2aedc0ae216bbcc91b8be224132530e389c"
    )


@pytest.fixture(scope="module")
def gate4_codec():
    return _frozen_module(
        4, "serialization", "fbf5617e3b07e2b2826550af08a8758e1ed5f7e43c9981acb9309dd2648a3c0d"
    )


@pytest.fixture(scope="module", params=["gate3_codec", "gate4_codec"])
def reference_codec(request):
    return request.getfixturevalue(request.param)


class TestGate3PersistedCompatibility:
    # Limit the existing disposable application database fixture to this class.
    application_postgres = staticmethod(persistence_tests.application_postgres)
    staging_postgres = staticmethod(persistence_tests.staging_postgres)
    ingestion = staticmethod(persistence_tests.ingestion)
    batch = staticmethod(persistence_tests.batch)

    @pytest.mark.parametrize("gate3_writer", [True, False], ids=["old-write", "new-write"])
    def test_read_and_replay(self, gate3_codec, gate3_writer, ingestion, batch):
        with pytest.MonkeyPatch.context() as patch:
            patch.setitem(sys.modules, "etl.serialization", gate3_codec)
            services = {
                "serialization": gate3_codec,
                "staging": _frozen_module(
                    3,
                    "staging",
                    "3d9a235e9bacc2a4bd76391c4a450d237fd28ec57772c796406123f56c68e3a5",
                ),
                "staging_reader": _frozen_module(
                    3,
                    "staging_reader",
                    "e1d9407751a08e64c052f7cc7d79486e9da6a4497a4c82d39b3c6b91da272e44",
                ),
            }
        reader_tests.test_cross_version_read_and_replay(services, gate3_writer, ingestion, batch)


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
    restored, digest = serialization._decode_and_fingerprint(json.loads(wire))
    assert digest == fingerprint_source_batch(batch)
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


class _TextSubclass(str):
    pass


class _AliasKey(str):
    """Looks up as 'value' in a Python dict but renders its actual text in JSON."""

    def __hash__(self):
        return hash("value")

    def __eq__(self, other):
        return str(other) == "value"


def test_positive_match_is_restricted_on_both_sides():
    class DictSubclass(dict):
        def items(self):
            pytest.fail("A dict subclass was inspected")

    match = serialization._same_flat_string_token
    plain = {"type": "str", "value": "اردو\x00"}
    assert match(plain, dict(reversed(plain.items())))
    assert not match(plain, {**plain, "value": "different"})
    for left, right in (
        (DictSubclass(plain), plain),
        ({_TextSubclass("type"): "str"}, {"type": "str"}),
        ({"value": _TextSubclass("x")}, {"value": "x"}),
        ({_AliasKey("disguised"): "x"}, {"value": "x"}),
        ({"value": False}, {"value": 0}),
        ({"value": 1.0}, {"value": 1}),
        ({"value": ["x"]}, {"value": ["x"]}),
    ):
        assert not match(left, right)
        assert not match(right, left)


def test_scalar_fallback_preserves_json_operands_and_error_path(monkeypatch):
    token = {"type": "numpy", "dtype": "bool", "bits": "02"}
    render = serialization._json
    seen = []

    def observe(value):
        seen.append(value)
        return render(value)

    monkeypatch.setattr(serialization, "_json", observe)
    with pytest.raises(SerializationError) as caught:
        serialization._read_scalar(token, "row[2].column[4]")
    assert str(caught.value) == (
        "SER-v1 row[2].column[4]: noncanonical or unreconstructable scalar representation"
    )
    assert seen == [{"type": "numpy", "dtype": "bool", "bits": "01"}, token]
    assert seen[1] is token


@pytest.mark.parametrize(
    ("token", "valid"),
    [
        ({"value": "e\u0301\x00", "type": "str"}, True),
        ({"type": "str", _TextSubclass("value"): "x"}, True),
        ({"type": "str", _AliasKey("disguised"): "x"}, False),
        ({"type": "str", "value": _TextSubclass("x")}, False),
        ({"type": "str", "value": "\ud800"}, False),
        ({"type": "str", "value": "x", "extra": ""}, False),
        ({"type": "str"}, False),
        ({"type": "unknown"}, False),
        ({"value": "01", "type": "int"}, False),
        ({"type": "numpy", "dtype": "bool", "bits": "02"}, False),
        ({"type": "bool", "value": 1}, False),
        ({"type": "decimal", "sign": False, "digits": [0], "exponent": 0}, False),
        ({"type": "decimal", "sign": 0.0, "digits": [0], "exponent": 0}, False),
        ({"type": "decimal", "sign": 0, "digits": [0, 1], "exponent": 0}, False),
    ],
    ids=[
        "reordered",
        "subclass-key",
        "alias-key",
        "subclass-value",
        "surrogate",
        "extra-key",
        "missing-key",
        "unknown-tag",
        "integer-text",
        "numpy-bits",
        "bool-int",
        "decimal-bool",
        "decimal-float",
        "decimal-digits",
    ],
)
def test_frozen_token_acceptance_and_error_contract(reference_codec, token, valid):
    payload = reference_codec.encode_source_batch(_batch())
    payload["records"][0]["values"][-1] = token
    before = json.dumps(payload, sort_keys=True)
    if valid:
        old = reference_codec.decode_source_batch(payload)
        new = decode_source_batch(payload)
        assert serialization._json(encode_source_batch(new)) == reference_codec._json(
            reference_codec.encode_source_batch(old)
        )
        assert fingerprint_source_batch(new) == reference_codec.fingerprint_source_batch(old)
    else:
        with pytest.raises(SerializationError) as old_error:
            reference_codec.decode_source_batch(payload)
        with pytest.raises(SerializationError) as new_error:
            decode_source_batch(payload)
        assert type(new_error.value) is type(old_error.value)
        assert str(new_error.value) == str(old_error.value)
        assert "row[0].column[8]" in str(new_error.value)
    assert json.dumps(payload, sort_keys=True) == before


def test_frozen_and_candidate_read_each_others_exact_representations(reference_codec):
    values = [
        0.0,
        -0.0,
        struct.unpack(">d", bytes.fromhex("fff8000000000001"))[0],
        np.array([0x7FC00003], dtype=np.uint32).view(np.float32)[0],
        np.float16(-0.0),
        Decimal("-0.0100"),
        Decimal("-0.00"),
        Decimal("-sNaN123"),
        Decimal("Infinity"),
        pd.Timestamp("2026-11-08 14:30:00.123456789", tz="Asia/Karachi"),
        datetime(2025, 11, 2, 1, 30, tzinfo=ZoneInfo("America/New_York"), fold=1),
        np.datetime64("2026-11-08T14:30:00.123", "ms"),
        "اردو\x00",
        None,
        pd.NA,
        pd.NaT,
    ]
    raw = _batch(values, index=pd.Index(["duplicate\x00"] * len(values), name="index"))
    raw.data.insert(
        len(raw.data.columns),
        "extra",
        pd.array([1, None] * 8, dtype="Int64"),
        allow_duplicates=True,
    )
    raw.data["flag"] = pd.array([True, None] * 8, dtype="boolean")
    raw.data["text"] = pd.array(["", None] * 8, dtype="string[python]")
    raw.data["price"] = pd.array([-0.0, None] * 8, dtype="Float64")
    old_wire = reference_codec.encode_source_batch(raw)
    before = reference_codec._json(old_wire).encode("utf-8")
    expected_hash = reference_codec.fingerprint_source_batch(raw)
    new_wire = encode_source_batch(raw)
    for codec, wire in ((serialization, old_wire), (reference_codec, new_wire)):
        restored = codec.decode_source_batch(wire)
        assert codec._json(codec.encode_source_batch(restored)).encode("utf-8") == before
        assert codec.fingerprint_source_batch(restored) == expected_hash
    assert serialization._json(new_wire).encode("utf-8") == before
    assert reference_codec._json(old_wire).encode("utf-8") == before
    assert reference_codec._json(reference_codec.encode_source_batch(raw)).encode() == before


@pytest.mark.parametrize("key_type", [str, _TextSubclass])
def test_post_dtype_regenerates_once_and_requires_exact_key_types(
    gate4_codec, monkeypatch, key_type
):
    payload = gate4_codec.encode_source_batch(_batch())
    supplied = {key_type("value"): "sample", "type": "str"}
    payload["records"][0]["values"][-1] = supplied
    old = gate4_codec.decode_source_batch(payload)
    scalar, render = serialization._scalar, serialization._json
    site = serialization._decode.__code__
    regenerated = []
    rendered = []

    def observe_scalar(value, path):
        if sys._getframe(1).f_code is site and path == "row[0].column[8]":
            regenerated.append(value)
        return scalar(value, path)

    def observe_json(value):
        if sys._getframe(1).f_code is site and value is supplied:
            rendered.append(value)
        return render(value)

    monkeypatch.setattr(serialization, "_scalar", observe_scalar)
    monkeypatch.setattr(serialization, "_json", observe_json)
    new = decode_source_batch(payload)
    assert regenerated == ["sample"]
    assert len(rendered) == (0 if key_type is str else 1)
    assert render(encode_source_batch(new)) == render(gate4_codec.encode_source_batch(old))


@pytest.mark.parametrize(
    "value",
    [np.int32(1), True, 1.0, -0.0, Decimal("1.00")],
    ids=["numpy-width", "bool", "float", "signed-zero", "decimal-scale"],
)
def test_post_dtype_mismatch_matches_gate4_and_compares_in_order(gate4_codec, monkeypatch, value):
    # Both tokens are valid before construction. Only row 1 changes in the int64 column.
    payload = gate4_codec.encode_source_batch(_batch([np.int64(1), value]))
    payload["dtypes"][-1] = {"kind": "numpy", "name": "int64"}
    supplied = payload["records"][1]["values"][-1]
    before = gate4_codec._json(payload)
    with pytest.raises(SerializationError) as old:
        gate4_codec.decode_source_batch(payload)

    seen = []
    render = serialization._json

    def observe(token):
        seen.append(token)
        return render(token)

    monkeypatch.setattr(serialization, "_json", observe)
    with pytest.raises(SerializationError) as new:
        decode_source_batch(payload)
    path = "row[1].column[8]"
    assert type(new.value) is type(old.value)
    expected_error = f"SER-v1 {path}: values incompatible with declared dtype"
    assert str(new.value) == str(old.value) == expected_error
    assert seen[-2] == gate4_codec._scalar(np.int64(value), path)
    assert seen[-1] is supplied
    assert render(payload) == before


def test_pre_comparison_array_construction_failure_matches_gate4(gate4_codec):
    payload = gate4_codec.encode_source_batch(_batch([np.int64(1), "1"]))
    payload["dtypes"][-1] = {"kind": "numpy", "name": "int64"}
    before = gate4_codec._json(payload)
    for codec in (gate4_codec, serialization):
        with pytest.raises(SerializationError) as caught:
            codec.decode_source_batch(payload)
        assert type(caught.value) is SerializationError
        assert str(caught.value) == "SER-v1 payload: malformed or incompatible representation"
        assert codec._json(payload) == before


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
    snapshot, digest = serialization._encode_and_fingerprint(_batch())
    assert snapshot == payload
    assert digest == "34945b7ea9a48eae6d466d54af358b02611b4aac8807054d5ad375af2f71339d"
    assert serialization._decode_and_fingerprint(payload)[1] == digest


@pytest.mark.parametrize("value", [None, [], "json text", 1, True])
def test_public_entry_points_reject_incompatible_contracts(value):
    with pytest.raises(SerializationError):
        encode_source_batch(value)
    with pytest.raises(SerializationError):
        decode_source_batch(value)
    with pytest.raises(SerializationError):
        fingerprint_source_batch(value)


def test_fingerprint_canonical_bytes_determinism_and_provenance_exclusion(monkeypatch):
    batch = _batch(["اردو", Decimal("-0.00")])
    payload = encode_source_batch(batch)
    content = payload.copy()
    del content["extracted_at"], content["batch_number"]
    canonical = json.dumps(
        content, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    expected = hashlib.sha256(canonical).hexdigest()
    sha256 = hashlib.sha256
    hashed = []

    def observe(data):
        hashed.append(data)
        return sha256(data)

    with monkeypatch.context() as patch:
        patch.setattr(serialization.hashlib, "sha256", observe)
        snapshot, digest = serialization._encode_and_fingerprint(batch)
        restored, read_digest = serialization._decode_and_fingerprint(payload)
    assert hashed == [canonical, canonical]
    assert read_digest == expected and encode_source_batch(restored) == snapshot
    assert snapshot == payload and digest == expected
    assert fingerprint_source_batch(batch) == expected
    assert fingerprint_source_batch(decode_source_batch(encode_source_batch(batch))) == expected
    assert (
        fingerprint_source_batch(
            replace(batch, batch_number=999, extracted_at=datetime(2000, 1, 1))
        )
        == expected
    )
    assert len(expected) == 64


@pytest.mark.parametrize("damage", [None, "noncanonical_int", "extracted_at"])
def test_persisted_payload_is_decoded_once_before_hashing(monkeypatch, damage):
    payload = encode_source_batch(_batch())
    if damage == "noncanonical_int":
        payload["records"][0]["values"][0] = {"type": "int", "value": "01"}
    elif damage == "extracted_at":
        payload["extracted_at"] = {"type": "none"}
    before = copy.deepcopy(payload)
    calls = []
    decode = serialization.decode_source_batch
    fingerprint = serialization._fingerprint_validated_payload

    def observed_decode(value):
        assert value is payload
        calls.append("decode")
        return decode(value)

    def observed_hash(value):
        assert value is payload
        calls.append("hash")
        return fingerprint(value)

    monkeypatch.setattr(serialization, "decode_source_batch", observed_decode)
    monkeypatch.setattr(serialization, "_fingerprint_validated_payload", observed_hash)
    if damage is None:
        restored, _ = serialization._decode_and_fingerprint(payload)
        restored.data.iloc[0, 0] = "caller edit"
        assert calls == ["decode", "hash"]
    else:
        with pytest.raises(SerializationError):
            serialization._decode_and_fingerprint(payload)
        assert calls == ["decode"]
    assert payload == before


def test_snapshot_is_captured_once_and_is_independent_of_later_edits(monkeypatch):
    batch = _batch([Decimal("1.00")], index=pd.Index(["original"]))
    expected = encode_source_batch(batch)
    original_digest = fingerprint_source_batch(batch)
    captures = []

    def capture_then_edit(source):
        payload = encode_source_batch(source)
        captures.append(payload)
        source.data.iat[0, -1] = Decimal("1.0")
        source.data.index = pd.Index(["edited"])
        return payload

    with monkeypatch.context() as patch:
        patch.setattr(serialization, "encode_source_batch", capture_then_edit)
        snapshot, digest = serialization._encode_and_fingerprint(batch)
    assert captures == [expected] and snapshot == expected
    assert digest == original_digest
    assert fingerprint_source_batch(batch) != digest


def test_paired_operation_rejects_an_unreconstructable_encoding(monkeypatch):
    core_encode = serialization._encode

    def invalid_position(source):
        payload = core_encode(source)
        payload["records"][0]["row_position"] = True
        return payload

    monkeypatch.setattr(serialization, "_encode", invalid_position)
    with pytest.raises(SerializationError, match="row.*expected an integer"):
        serialization._encode_and_fingerprint(_batch())


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
