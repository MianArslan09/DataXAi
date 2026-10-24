"""SER-v1: bounded, lossless serialization of the representation at SourceBatch.

This is a codec, not ingestion identity, storage, or transformation. Public entry
points raise SerializationError with positional paths, without logging source values.
The payload is ordinary JSON: type-tagged cells, positional columns/dtypes, index
metadata, and records containing row_position, index_label and values. Unknown keys
and noncanonical representations are rejected, as are lossy dtype constructions.

Supported scalars: exact Python str/bool/int/float/date/datetime/Decimal; NumPy
bool, signed/unsigned 8/16/32/64-bit integers (including intc/uintc), binary16/32/64
floats; None, NA, NaT;
Timestamp (s/ms/us/ns); datetime64 (Y/M/W/D/h/m/s/ms/us/ns, multiplier 1).
Floats use big-endian fixed-width hex bits. Decimals use sign/digits/exponent.
Datetimes retain wall components, fold and timezone; Timestamps retain epoch ticks,
resolution, fold and timezone. Zones are datetime.timezone, ZoneInfo and pandas'
pytz IANA zones (including UTC), never arbitrary tzinfo objects. IANA decoding
requires matching installed timezone rules; recorded offsets detect rule drift.
NumPy type names are retained, including platform C integer distinctions: a
receiver that collapses a distinct type/alias rejects it instead of coercing it.

Frames support object, native NumPy bool/int/uint/float, datetime64[s/ms/us/ns]
(including timezone dtypes), nullable Int64/Float64/boolean and string[python].
Object cells keep their runtime scalar types independently of the column dtype.
Axes support RangeIndex, plain Index and DatetimeIndex without frequency metadata;
axis names are None or strings. Columns must be strings, possibly duplicated.
Frame attrs, categorical/other extension arrays, MultiIndex, custom subclasses,
nested objects, nonnative-endian column arrays and other dtypes are unsupported.
The allows_duplicate_labels flag is retained. Metadata requires a string source
name, nonnegative Python integer batch number and Python datetime or Timestamp.

Fingerprint bytes are UTF-8 JSON (ensure_ascii=False, sort_keys=True,
separators=(',', ':'), allow_nan=False), excluding ONLY batch_number/extracted_at.
No nonstandard JSON NaN/Infinity literals occur. Integers/ticks use decimal strings
to avoid JSON number precision loss; row positions/counts/version remain integers.
Memory is bounded by the supplied batch, not by the source dataset.
"""

import hashlib
import json
import re
import struct
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pandas as pd
import pytz

from etl.exceptions import SerializationError
from etl.sources.contract import CANONICAL_COLUMNS, SourceBatch

__all__ = ["encode_source_batch", "decode_source_batch", "fingerprint_source_batch"]

_FORMAT = "dataxai.source-batch"
_UNITS = {"Y", "M", "W", "D", "h", "m", "s", "ms", "us", "ns"}
_PANDAS_UNITS = {"s", "ms", "us", "ns"}
_NUMERIC = {
    "bool",
    *(f"{kind}{n}" for kind in ("int", "uint") for n in (8, 16, 32, 64)),
    "intc",
    "uintc",
    "float16",
    "float32",
    "float64",
}
_NULLABLE = {
    "Int64": pd.Int64Dtype(),
    "Float64": pd.Float64Dtype(),
    "boolean": pd.BooleanDtype(),
    "string": pd.StringDtype(storage="python"),
}
_PAYLOAD_KEYS = {
    "format",
    "version",
    "source_name",
    "batch_number",
    "extracted_at",
    "columns",
    "columns_name",
    "columns_dtype",
    "dtypes",
    "index",
    "row_count",
    "records",
    "allows_duplicate_labels",
}


def _require(condition, path, reason):
    if not condition:
        raise SerializationError(f"SER-v1 {path}: {reason}")


def _keys(value, keys, path):
    _require(type(value) is dict and set(value) == set(keys), path, "invalid object fields")


def _text(value, path):
    _require(type(value) is str, path, "expected a string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise SerializationError(f"SER-v1 {path}: string is not valid UTF-8") from None
    return value


def _integer(value, path, minimum=None):
    _require(type(value) is int, path, "expected an integer")
    _require(minimum is None or value >= minimum, path, "integer outside supported range")
    return value


def _decimal_integer(value, path):
    _text(value, path)
    _require(re.fullmatch(r"0|-?[1-9][0-9]*", value) is not None, path, "invalid integer text")
    return int(value)


def _name(value, path):
    return None if value is None else _text(value, path)


def _zone(tz, path):
    if tz is None:
        return None
    if type(tz) is timezone:
        return {
            "kind": "offset",
            "microseconds": _offset(tz.utcoffset(None)),
            "name": _text(tz.tzname(None), path),
        }
    if type(tz) is ZoneInfo:
        return {"kind": "zoneinfo", "key": _text(tz.key, path)}
    if isinstance(tz, pytz.BaseTzInfo) and type(tz) is type(pytz.timezone(tz.zone)):
        return {"kind": "pytz", "key": _text(tz.zone, path)}
    raise SerializationError(f"SER-v1 {path}: unsupported timezone")


def _read_zone(value, path):
    if value is None:
        return None
    _require(type(value) is dict, path, "expected timezone object")
    kind = value.get("kind")
    if kind == "offset":
        _keys(value, {"kind", "microseconds", "name"}, path)
        return timezone(
            timedelta(microseconds=_integer(value["microseconds"], path)),
            _text(value["name"], path),
        )
    _keys(value, {"kind", "key"}, path)
    key = _text(value["key"], path)
    if kind == "zoneinfo":
        return ZoneInfo(key)
    if kind == "pytz":
        return pytz.timezone(key)
    raise SerializationError(f"SER-v1 {path}: unsupported timezone kind")


def _offset(value):
    return None if value is None else value // timedelta(microseconds=1)


def _scalar(value, path):
    if value is None:
        return {"type": "none"}
    if value is pd.NA:
        return {"type": "na"}
    if value is pd.NaT:
        return {"type": "nat"}
    if type(value) is str:
        return {"type": "str", "value": _text(value, path)}
    if type(value) is bool:
        return {"type": "bool", "value": value}
    if type(value) is int:
        return {"type": "int", "value": str(value)}
    if type(value) is float:
        return {"type": "float", "bits": struct.pack(">d", value).hex()}
    if type(value) is Decimal:
        sign, digits, exponent = value.as_tuple()
        return {"type": "decimal", "sign": sign, "digits": list(digits), "exponent": exponent}
    if type(value) is np.datetime64:
        unit, multiplier = np.datetime_data(value.dtype)
        _require(unit in _UNITS and multiplier == 1, path, "unsupported datetime64 unit")
        return {"type": "np_datetime", "unit": unit, "ticks": str(int(value.view("i8")))}
    if isinstance(value, np.generic) and value.dtype.type.__name__ in _NUMERIC:
        _require(type(value) is value.dtype.type, path, "unsupported NumPy scalar subclass")
        dtype = value.dtype
        bits = np.asarray(value, dtype=dtype.newbyteorder(">")).tobytes().hex()
        return {"type": "numpy", "dtype": dtype.type.__name__, "bits": bits}
    if type(value) is pd.Timestamp:
        _require(value.unit in _PANDAS_UNITS, path, "unsupported Timestamp unit")
        return {
            "type": "timestamp",
            "unit": value.unit,
            "ticks": str(int(value.asm8.view("i8"))),
            "zone": _zone(value.tzinfo, path),
            "offset": _offset(value.utcoffset()),
            "fold": value.fold,
        }
    if type(value) is datetime:
        return {
            "type": "datetime",
            "parts": [
                value.year,
                value.month,
                value.day,
                value.hour,
                value.minute,
                value.second,
                value.microsecond,
            ],
            "fold": value.fold,
            "zone": _zone(value.tzinfo, path),
            "offset": _offset(value.utcoffset()),
        }
    if type(value) is date:
        return {"type": "date", "parts": [value.year, value.month, value.day]}
    raise SerializationError(f"SER-v1 {path}: unsupported scalar type")


def _read_scalar(value, path):
    _require(type(value) is dict and type(value.get("type")) is str, path, "invalid scalar tag")
    tag = value["type"]
    if tag in {"none", "na", "nat"}:
        _keys(value, {"type"}, path)
        result = {"none": None, "na": pd.NA, "nat": pd.NaT}[tag]
    elif tag in {"str", "bool", "int"}:
        _keys(value, {"type", "value"}, path)
        if tag == "str":
            result = _text(value["value"], path)
        elif tag == "bool":
            _require(type(value["value"]) is bool, path, "expected Boolean")
            result = value["value"]
        else:
            result = _decimal_integer(value["value"], path)
    elif tag in {"float", "numpy"}:
        _keys(value, {"type", "bits"} | ({"dtype"} if tag == "numpy" else set()), path)
        dtype = np.dtype("float64")
        if tag == "numpy":
            name = _text(value["dtype"], path)
            _require(name in _NUMERIC, path, "unsupported scalar dtype")
            dtype = np.dtype(name)
        bits = _text(value["bits"], path)
        _require(
            re.fullmatch("[0-9a-f]{" + str(dtype.itemsize * 2) + "}", bits) is not None,
            path,
            "invalid fixed-width bits",
        )
        raw = bytes.fromhex(bits)
        result = (
            struct.unpack(">d", raw)[0]
            if tag == "float"
            else np.frombuffer(raw, dtype=dtype.newbyteorder(">"))[0]
        )
    elif tag == "decimal":
        _keys(value, {"type", "sign", "digits", "exponent"}, path)
        _require(_integer(value["sign"], path) in (0, 1), path, "invalid Decimal sign")
        digits = value["digits"]
        _require(
            type(digits) is list and all(type(d) is int and 0 <= d <= 9 for d in digits),
            path,
            "invalid Decimal digits",
        )
        exponent = value["exponent"]
        _require(
            type(exponent) is int or (type(exponent) is str and exponent in {"F", "n", "N"}),
            path,
            "invalid Decimal exponent",
        )
        result = Decimal((value["sign"], tuple(digits), exponent))
    elif tag in {"datetime", "date"}:
        _keys(
            value,
            {"type", "parts"} | ({"zone", "offset", "fold"} if tag == "datetime" else set()),
            path,
        )
        parts = value["parts"]
        _require(
            type(parts) is list
            and len(parts) == (7 if tag == "datetime" else 3)
            and all(type(p) is int for p in parts),
            path,
            "invalid calendar components",
        )
        result = date(*parts) if tag == "date" else datetime(*parts)
        if tag == "datetime":
            tz = _read_zone(value["zone"], path)
            fold = _integer(value["fold"], path)
            if isinstance(tz, pytz.BaseTzInfo):
                # Reconstruct the particular localized pytz transition, not its LMT base offset.
                offset = _integer(value["offset"], path)
                result = (result - timedelta(microseconds=offset)).replace(tzinfo=tz)
                result = tz.fromutc(result).replace(fold=fold)
            else:
                result = result.replace(tzinfo=tz, fold=fold)
    elif tag in {"timestamp", "np_datetime"}:
        _keys(
            value,
            {"type", "unit", "ticks"}
            | ({"zone", "offset", "fold"} if tag == "timestamp" else set()),
            path,
        )
        unit = _text(value["unit"], path)
        _require(
            unit in (_PANDAS_UNITS if tag == "timestamp" else _UNITS),
            path,
            "unsupported datetime unit",
        )
        ticks = _decimal_integer(value["ticks"], path)
        _require(-(2**63) <= ticks < 2**63, path, "datetime ticks out of bounds")
        result = np.datetime64(ticks, unit)
        if tag == "timestamp":
            _require(ticks != -(2**63), path, "Timestamp cannot encode NaT ticks")
            result = pd.Timestamp(result)
            tz = _read_zone(value["zone"], path)
            if tz is not None:
                result = result.tz_localize(timezone.utc).tz_convert(tz)
            result = result.replace(fold=_integer(value["fold"], path)).as_unit(unit)
    else:
        raise SerializationError(f"SER-v1 {path}: unsupported scalar tag")
    # Exact JSON comparison also distinguishes bool from int in malformed metadata.
    _require(
        _json(_scalar(result, path)) == _json(value),
        path,
        "noncanonical or unreconstructable scalar representation",
    )
    return result


def _dtype(dtype, path):
    if isinstance(dtype, np.dtype):
        if dtype == np.dtype("object"):
            return {"kind": "object"}
        if dtype.isnative and dtype.type.__name__ in _NUMERIC:
            return {"kind": "numpy", "name": dtype.type.__name__}
        if dtype.kind == "M" and dtype.isnative:
            unit, multiplier = np.datetime_data(dtype)
            if unit in _PANDAS_UNITS and multiplier == 1:
                return {"kind": "datetime", "unit": unit, "zone": None}
    if type(dtype) is pd.DatetimeTZDtype and dtype.unit in _PANDAS_UNITS:
        return {"kind": "datetime", "unit": dtype.unit, "zone": _zone(dtype.tz, path)}
    for name, supported in _NULLABLE.items():
        if type(dtype) is type(supported) and dtype == supported:
            return {"kind": "nullable", "name": name}
    raise SerializationError(f"SER-v1 {path}: unsupported dtype")


def _read_dtype(value, path):
    _require(type(value) is dict, path, "expected dtype object")
    kind = value.get("kind")
    if kind == "object":
        _keys(value, {"kind"}, path)
        return np.dtype("object")
    if kind in {"numpy", "nullable"}:
        _keys(value, {"kind", "name"}, path)
        name = _text(value["name"], path)
        _require(name in (_NUMERIC if kind == "numpy" else _NULLABLE), path, "unsupported dtype")
        return np.dtype(name) if kind == "numpy" else _NULLABLE[name]
    if kind == "datetime":
        _keys(value, {"kind", "unit", "zone"}, path)
        unit = _text(value["unit"], path)
        _require(unit in _PANDAS_UNITS, path, "unsupported datetime dtype unit")
        tz = _read_zone(value["zone"], path)
        return (
            np.dtype(f"datetime64[{unit}]") if tz is None else pd.DatetimeTZDtype(unit=unit, tz=tz)
        )
    raise SerializationError(f"SER-v1 {path}: unsupported dtype kind")


def _index(index):
    name = _name(index.name, "index.name")
    if type(index) is pd.RangeIndex:
        return {
            "kind": "range",
            "name": name,
            "start": str(index.start),
            "stop": str(index.stop),
            "step": str(index.step),
        }
    _require(type(index) in (pd.Index, pd.DatetimeIndex), "index", "unsupported index class")
    if type(index) is pd.DatetimeIndex:
        _require(index.freq is None, "index", "frequency metadata is unsupported")
    return {
        "kind": "datetime" if type(index) is pd.DatetimeIndex else "index",
        "name": name,
        "dtype": _dtype(index.dtype, "index.dtype"),
        "labels": [_scalar(v, f"index[{i}]") for i, v in enumerate(index.array)],
    }


def _array(values, dtype):
    # Avoid NumPy/pandas inference for object cells (especially dates, NaNs and mixed scalars).
    raw = np.empty(len(values), dtype=object)
    for i, value in enumerate(values):
        raw[i] = value
    if dtype == np.dtype("object"):
        return raw
    if type(dtype) is pd.Float64Dtype:
        # FloatingArray can contain a visible NaN distinct from its masked NA.
        # Series(..., dtype="Float64") would silently mask that NaN.
        mask = np.array([v is pd.NA for v in values], dtype=bool)
        data = np.array(
            [0.0 if missing else v for v, missing in zip(values, mask, strict=True)],
            dtype=np.float64,
        )
        return pd.arrays.FloatingArray(data, mask)
    return pd.Series(raw, dtype=dtype).array


def _read_index(value, count):
    _require(type(value) is dict, "index", "expected index object")
    kind = value.get("kind")
    if kind == "range":
        _keys(value, {"kind", "name", "start", "stop", "step"}, "index")
        args = [_decimal_integer(value[k], "index." + k) for k in ("start", "stop", "step")]
        result = pd.RangeIndex(*args, name=_name(value["name"], "index.name"))
    else:
        _keys(value, {"kind", "name", "dtype", "labels"}, "index")
        _require(kind in {"index", "datetime"}, "index", "unsupported index kind")
        labels = value["labels"]
        _require(type(labels) is list and len(labels) == count, "index", "label count mismatch")
        dtype = _read_dtype(value["dtype"], "index.dtype")
        array = _array([_read_scalar(v, f"index[{i}]") for i, v in enumerate(labels)], dtype)
        result = pd.Index(array, dtype=dtype, name=_name(value["name"], "index.name"))
    _require(len(result) == count, "index", "row count mismatch")
    _require(
        _json(_index(result)) == _json(value), "index", "index construction changed representation"
    )
    return result


def _json(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _encode(batch):
    _require(type(batch) is SourceBatch, "batch", "expected SourceBatch")
    frame = batch.data
    _require(type(frame) is pd.DataFrame, "data", "expected DataFrame")
    _require(not frame.attrs, "data", "DataFrame attrs are unsupported")
    _require(type(frame.columns) is pd.Index, "columns", "unsupported columns index")
    columns = [_text(c, f"columns[{i}]") for i, c in enumerate(frame.columns)]
    _require(set(CANONICAL_COLUMNS) <= set(columns), "columns", "missing canonical source columns")
    _require(
        type(batch.extracted_at) in (datetime, pd.Timestamp),
        "extracted_at",
        "expected datetime or Timestamp",
    )
    arrays = [frame.iloc[:, c].array for c in range(len(columns))]
    return {
        "format": _FORMAT,
        "version": 1,
        "source_name": _text(batch.source_name, "source_name"),
        "batch_number": _integer(batch.batch_number, "batch_number", 0),
        "extracted_at": _scalar(batch.extracted_at, "extracted_at"),
        "columns": columns,
        "columns_name": _name(frame.columns.name, "columns_name"),
        "columns_dtype": _dtype(frame.columns.dtype, "columns_dtype"),
        "dtypes": [_dtype(d, f"dtypes[{i}]") for i, d in enumerate(frame.dtypes)],
        "index": _index(frame.index),
        "row_count": len(frame),
        "allows_duplicate_labels": frame.flags.allows_duplicate_labels,
        "records": [
            {
                "row_position": i,
                "index_label": _scalar(frame.index[i], f"row[{i}].index"),
                "values": [_scalar(a[i], f"row[{i}].column[{c}]") for c, a in enumerate(arrays)],
            }
            for i in range(len(frame))
        ],
    }


def _decode(payload):
    _keys(payload, _PAYLOAD_KEYS, "payload")
    _require(
        payload["format"] == _FORMAT
        and type(payload["version"]) is int
        and payload["version"] == 1,
        "payload",
        "unsupported format or version",
    )
    count = _integer(payload["row_count"], "row_count", 0)
    columns = payload["columns"]
    _require(type(columns) is list, "columns", "expected a list")
    for i, label in enumerate(columns):
        _text(label, f"columns[{i}]")
    _require(set(CANONICAL_COLUMNS) <= set(columns), "columns", "missing canonical source columns")
    dtypes = payload["dtypes"]
    _require(
        type(dtypes) is list and len(dtypes) == len(columns), "dtypes", "column count mismatch"
    )
    dtypes = [_read_dtype(d, f"dtypes[{i}]") for i, d in enumerate(dtypes)]
    index = _read_index(payload["index"], count)
    records = payload["records"]
    _require(type(records) is list and len(records) == count, "records", "row count mismatch")
    values = [[] for _ in columns]
    for i, record in enumerate(records):
        path = f"row[{i}]"
        _keys(record, {"row_position", "index_label", "values"}, path)
        _require(_integer(record["row_position"], path) == i, path, "invalid row position")
        _require(
            _json(record["index_label"]) == _json(_scalar(index[i], path)),
            path,
            "index label does not match index structure",
        )
        cells = record["values"]
        _require(type(cells) is list and len(cells) == len(columns), path, "field count mismatch")
        for c, cell in enumerate(cells):
            values[c].append(_read_scalar(cell, f"{path}.column[{c}]"))
    series = []
    for c, (items, dtype) in enumerate(zip(values, dtypes, strict=True)):
        array = _array(items, dtype)
        for i, value in enumerate(array):
            path = f"row[{i}].column[{c}]"
            _require(
                _json(_scalar(value, path)) == _json(records[i]["values"][c]),
                path,
                "values incompatible with declared dtype",
            )
        series.append(pd.Series(array, dtype=dtype))
    frame = pd.concat(series, axis=1)
    frame.columns = pd.Index(
        columns,
        dtype=_read_dtype(payload["columns_dtype"], "columns_dtype"),
        name=_name(payload["columns_name"], "columns_name"),
    )
    frame.index = index
    _require(
        type(payload["allows_duplicate_labels"]) is bool,
        "allows_duplicate_labels",
        "expected Boolean",
    )
    frame.flags.allows_duplicate_labels = payload["allows_duplicate_labels"]
    extracted_at = _read_scalar(payload["extracted_at"], "extracted_at")
    _require(type(extracted_at) in (datetime, pd.Timestamp), "extracted_at", "expected datetime")
    result = SourceBatch(
        frame,
        _text(payload["source_name"], "source_name"),
        _integer(payload["batch_number"], "batch_number", 0),
        extracted_at,
    )
    _require(
        _json(_encode(result)) == _json(payload), "payload", "construction changed representation"
    )
    return result


# Catch only anticipated representation/construction failures, not arbitrary programming errors.
_REPRESENTATION_ERRORS = (
    ValueError,
    TypeError,
    OverflowError,
    InvalidOperation,
    ZoneInfoNotFoundError,
    pytz.UnknownTimeZoneError,
)


def encode_source_batch(batch: SourceBatch) -> dict:
    """Encode supported source content/provenance; fail atomically without changing input.

    Reconstruct once to ensure this pandas/runtime combination can honor the wire
    contract. This bounded batch-level check prevents emitting unrecoverable data.
    """
    try:
        payload = _encode(batch)
        _decode(payload)
        return payload
    except _REPRESENTATION_ERRORS:
        raise SerializationError(
            "SER-v1 batch: unsupported or unreconstructable representation"
        ) from None


def decode_source_batch(payload: dict) -> SourceBatch:
    """Validate SER-v1 and reconstruct a fresh batch, rejecting casts or malformed data."""
    try:
        return _decode(payload)
    except _REPRESENTATION_ERRORS:
        raise SerializationError(
            "SER-v1 payload: malformed or incompatible representation"
        ) from None


def fingerprint_source_batch(batch: SourceBatch) -> str:
    """SHA-256 of canonical typed content; excludes batch number and extraction time.

    This content fingerprint does not replace logical ingestion/batch identity.
    """
    payload = encode_source_batch(batch)
    del payload["batch_number"]
    del payload["extracted_at"]
    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()
