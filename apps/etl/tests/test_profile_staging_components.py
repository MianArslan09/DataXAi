"""Database-free Gate 4 tests. Workbooks and source values here are synthetic."""

import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest
from django.core.exceptions import ValidationError
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.backends.utils import CursorWrapper
from django.db.models.query import QuerySet
from openpyxl import Workbook
from scripts import profile_staging_components as probe

from etl import serialization as ser
from etl.exceptions import SerializationError
from etl.models import StagingRecord
from etl.serialization import _index, encode_source_batch
from etl.sources import CANONICAL_COLUMNS, ExcelSource, SourceBatch

_PRIVATE = "synthetic-private-customer"
_DATE = "2026-11-08 09:20:00"
_SCRIPT = Path(probe.__file__).resolve()


@pytest.fixture(autouse=True)
def prohibit_database_access(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("The component probe attempted database access")

    for method in ("connect", "ensure_connection", "cursor", "chunked_cursor"):
        monkeypatch.setattr(BaseDatabaseWrapper, method, forbidden)
    for method in ("execute", "executemany"):
        monkeypatch.setattr(CursorWrapper, method, forbidden)
    for method in ("bulk_create", "_fetch_all"):
        monkeypatch.setattr(QuerySet, method, forbidden)


def _batch(index, dates):
    rows = [
        ["0001", "POST", "اردو\x00", -2, stamp, Decimal("-0.0100"), _PRIVATE, "Pakistan"]
        for stamp in dates
    ]
    frame = pd.DataFrame(rows, columns=CANONICAL_COLUMNS, index=index, dtype=object)
    return SourceBatch(frame, "synthetic:gate4", 0, datetime(2026, 11, 8, tzinfo=timezone.utc))


def _workbook(path, size, *, extra=False):
    book = Workbook(write_only=True)
    sheet = book.create_sheet("first")
    sheet.append(CANONICAL_COLUMNS)
    row = ["0001", "POST", "اردو", -2, _DATE, -0.01, _PRIVATE, "Pakistan"]
    for pos in range(size):
        sheet.append(row if pos else [*row[:4], None, *row[5:]])
    if extra:
        # Consuming this row would fail type conversion. Only batch zero is in scope.
        sheet.append([*row[:3], _PRIVATE, *row[4:]])
        book.create_sheet("unused").append(["unsupported header"])
    book.save(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _standalone(args, directory):
    # Invoke the real entry point with tripwires active even during Django startup.
    guard = """
import runpy, sys
import psycopg
from django.conf import settings
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.backends.utils import CursorWrapper
from sqlalchemy.engine import Engine

def deny(*args, **kwargs):
    raise SystemExit(91)

BaseDatabaseWrapper.connect = BaseDatabaseWrapper.cursor = deny
CursorWrapper.execute = CursorWrapper.executemany = deny
psycopg.connect = Engine.connect = deny
def audit(event, args):
    if event in {"socket.connect", "sqlite3.connect"}:
        deny()
sys.addaudithook(audit)
sys.argv = sys.argv[1:]
try:
    runpy.run_path(sys.argv[0], run_name="__main__")
finally:
    if settings.configured:
        assert set(settings.DATABASES) == {"default"}
        assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.dummy"
"""
    return subprocess.run(
        [sys.executable, "-B", "-c", guard, str(_SCRIPT), *map(str, args)],
        cwd=directory,
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "synthetic_settings_must_never_be_loaded"},
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )


@pytest.mark.parametrize("rows", [1000, 5000])
def test_standalone_profiles_first_pinned_synthetic_batch_without_a_database(tmp_path, rows):
    path = tmp_path / f"{_PRIVATE}.xlsx"
    digest = _workbook(path, rows, extra=True)
    result = _standalone(["--path", path, "--expected-sha256", digest, "--rows", rows], tmp_path)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert "fingerprint_breakdown" not in report
    assert report["schema"] == "dataxai.staging-components-v1"
    assert report["dataset_sha256"] == digest
    assert report["requested_rows"] == report["rows"] == rows
    assert (report["safe_rows"], report["unresolved_rows"]) == (rows - 1, 1)
    assert report["record_groups"] == rows // 1000 and report["max_rows_per_group"] == 1000
    assert report["database_operations"] == 0
    assert report["warmup_repetitions"] == 1 and report["measured_repetitions"] == 5
    assert set(report["components"]) == {
        "construct_records",
        "prepare_text",
        "convert_text",
        "select_split_indexes",
    }
    for name, component in report["components"].items():
        assert component["operations_per_run"] == (2 if name == "select_split_indexes" else rows)
        observations = component["observations"]
        assert [o["run"] for o in observations] == [1, 2, 3, 4, 5]
        for clock in ("cpu_ns", "wall_ns"):
            values = [o[clock] for o in observations]
            assert all(type(v) is int and v >= 0 for v in values)
            assert component[f"total_{clock}"] == sum(values)
            assert component[f"median_{clock}"] == sorted(values)[2]
    assert "0 database operations" in result.stderr
    for text in (_PRIVATE, "اردو", '"row_position":', str(tmp_path)):
        assert text not in result.stdout + result.stderr
    assert list(tmp_path.iterdir()) == [path]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize(
    ("index", "dates", "safe"),
    [
        (pd.RangeIndex(4, 10, 2, name="range"), [_DATE] * 3, (0, 1, 2)),
        (pd.Index(["same", "same", "different"], name="object"), [_DATE, None, _DATE], (0, 2)),
        (pd.Index(pd.array([4, pd.NA, 6], dtype="Int64"), name="nullable"), [None] * 3, ()),
        (
            pd.DatetimeIndex(
                ["2026-10-10 14:30:00.123456789", "2026-10-10", "2026-10-11"],
                tz="Asia/Karachi",
                name="clock",
            ),
            [_DATE, None, _DATE],
            (0, 2),
        ),
        (
            pd.Index([Decimal("1.0"), Decimal("1.00"), Decimal("-0.00")], dtype=object),
            [None, _DATE, _DATE],
            (1, 2),
        ),
        (pd.Index([0.0, -0.0, 3.0], name="zero"), [_DATE, None, _DATE], (0, 2)),
    ],
    ids=["range", "duplicate", "nullable", "timezone", "decimal", "signed-zero"],
)
def test_real_interpretation_splits_preserve_index_representation_and_input(index, dates, safe):
    batch = _batch(index, dates)
    before = encode_source_batch(batch)
    other = tuple(p for p in range(3) if p not in safe)
    assert probe._interpret_splits(batch) == (
        (safe, _index(batch.data.iloc[list(safe)].index)),
        (other, _index(batch.data.iloc[list(other)].index)),
    )
    report = probe._profile_batch(batch)
    assert (report["safe_rows"], report["unresolved_rows"]) == (len(safe), len(other))
    assert encode_source_batch(batch) == before
    assert _PRIVATE not in json.dumps(report)


def test_independent_windows_exclude_setup_and_count_a_short_final_group(monkeypatch):
    batch = _batch(pd.Index(["same"] * 3), [_DATE] * 3)
    monkeypatch.setattr(probe, "_GROUP_SIZE", 2)
    clock = [0]
    monkeypatch.setattr(probe.time, "perf_counter_ns", lambda: clock[0])
    monkeypatch.setattr(probe.time, "process_time_ns", lambda: clock[0])
    positions = []
    init = StagingRecord.__init__
    field = StagingRecord._meta.get_field("record_payload")
    prep, convert = field.get_prep_value, field.from_db_value

    def construct(obj, **kwargs):
        init(obj, **kwargs)
        positions.append(obj.row_position)
        clock[0] += 5

    def prepare(value):
        clock[0] += 10
        return prep(value)

    def restore(text, expression, connection):
        clock[0] += 100
        assert "\\u0000" in text and text.isascii()
        return convert(text, expression, connection)

    monkeypatch.setattr(StagingRecord, "__init__", construct)
    monkeypatch.setattr(field, "get_prep_value", prepare)
    monkeypatch.setattr(field, "from_db_value", restore)
    report = probe._profile_batch(batch)
    assert report["record_groups"] == 2
    assert positions == [0, 1] * 6 + [2] * 6
    assert Counter(positions) == {0: 6, 1: 6, 2: 6}
    # Conversion includes the field's own nested preparation. Setup prep is excluded.
    for name, expected in (("construct_records", 15), ("prepare_text", 30), ("convert_text", 330)):
        samples = report["components"][name]["observations"]
        assert [(s["wall_ns"], s["cpu_ns"]) for s in samples] == [(expected, expected)] * 5


def test_invalid_field_conversion_and_wrong_position_fail_closed(monkeypatch):
    field = StagingRecord._meta.get_field("record_payload")
    assert field.get_prep_value(None) is None and field.from_db_value(None, None, None) is None
    for text in ("null", "[]", '{"a":1,"a":2}', '{"a":NaN}'):
        with pytest.raises(ValidationError):
            field.from_db_value(text, None, None)
    with pytest.raises(ValidationError):
        field.get_prep_value([])

    with pytest.raises(probe._ProbeError, match="RECORD_POSITION_MISMATCH"):
        probe._measure_records([{"row_position": 1}], [])
    convert = field.from_db_value

    def change_scalar_type(*args):
        row = convert(*args)
        row["row_position"] = False  # Compares equal to Python integer zero.
        return row

    monkeypatch.setattr(field, "from_db_value", change_scalar_type)
    with pytest.raises(probe._ProbeError, match="RECORD_ROUNDTRIP_MISMATCH"):
        probe._profile_batch(_batch(pd.RangeIndex(1), [_DATE]))


def test_hash_rejection_precedes_excel_open_and_timing(tmp_path, monkeypatch):
    path = tmp_path / "synthetic.xlsx"
    path.write_bytes(b"not a workbook")
    monkeypatch.setattr(ExcelSource, "__enter__", lambda *args: pytest.fail("opened bad input"))
    with pytest.raises(probe._ProbeError, match="WORKBOOK_HASH_MISMATCH"):
        probe._first_batch(path, 1000, "0" * 64)


def test_same_workbook_selects_the_same_first_batch_in_order(tmp_path):
    path = tmp_path / "selection.xlsx"
    digest = _workbook(path, 3, extra=True)
    first = probe._first_batch(path, 3, digest)
    again = probe._first_batch(path, 3, digest)
    assert first.batch_number == again.batch_number == 0
    assert first.source_name == again.source_name == "xlsx:selection.xlsx:first"
    assert first.data.index.tolist() == [0, 1, 2]
    assert first.data["InvoiceDate"].tolist() == [None, _DATE, _DATE]
    pd.testing.assert_frame_equal(first.data, again.data, check_exact=True)


def test_changed_decimal_index_scale_is_rejected(monkeypatch):
    batch = _batch(pd.Index([Decimal("1.00")], dtype=object), [_DATE])
    interpret = probe._interpret_splits

    def changed_scale(source):
        (positions, _), unresolved = interpret(source)
        changed = pd.Index([Decimal("1.0")], dtype=object)
        assert source.data.index.equals(changed)
        return (positions, _index(changed)), unresolved

    monkeypatch.setattr(probe, "_interpret_splits", changed_scale)
    with pytest.raises(probe._ProbeError, match="INDEX_REPRESENTATION_MISMATCH"):
        probe._profile_batch(batch)


@pytest.mark.parametrize("size", [0, 3])
def test_short_or_empty_first_sheet_fails_without_combining_batches(tmp_path, size):
    path = tmp_path / "first.xlsx"
    digest = _workbook(path, size)
    with pytest.raises(probe._ProbeError, match="INCOMPLETE_FIRST_BATCH"):
        probe._first_batch(path, 1000, digest)


def test_empty_input_rejected():
    with pytest.raises(probe._ProbeError, match="INVALID_BATCH_SIZE"):
        probe._profile_batch(_batch(pd.RangeIndex(0), []))


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--rows", "999", "--expected-sha256", "0" * 64, "--path", _PRIVATE],
        ["--rows", "1000", "--expected-sha256", _PRIVATE, "--path", "unused"],
    ],
)
def test_cli_argument_errors_are_sanitized(arguments, capsys):
    with pytest.raises(SystemExit) as error:
        probe.main(arguments)
    assert error.value.code == 2
    output = capsys.readouterr()
    assert not output.out and _PRIVATE not in output.err


def test_cli_refuses_preconfigured_django_without_touching_input(capsys):
    assert probe.main(["--rows", "1000", "--expected-sha256", "0" * 64, "--path", _PRIVATE]) == 1
    output = capsys.readouterr()
    assert not output.out and "STANDALONE_PROCESS_REQUIRED" in output.err
    assert _PRIVATE not in output.err


def test_failed_standalone_checksum_has_no_partial_evidence(tmp_path):
    path = tmp_path / f"{_PRIVATE}.xlsx"
    _workbook(path, 3)
    result = _standalone(["--rows", 1000, "--expected-sha256", "0" * 64, "--path", path], tmp_path)
    assert result.returncode == 1 and not result.stdout
    assert "WORKBOOK_HASH_MISMATCH" in result.stderr and _PRIVATE not in result.stderr


@pytest.mark.parametrize("available", [True, False])
def test_peak_memory_has_process_scope_or_explicit_unavailability(monkeypatch, available):
    def measure():
        if available:
            return 123456
        raise OSError(_PRIVATE)

    monkeypatch.setattr(probe.sys, "platform", "win32")
    monkeypatch.setattr(probe, "_windows_peak_bytes", measure)
    memory = probe._process_memory()
    assert memory["metric"] == "peak_working_set" and "process-lifetime" in memory["scope"]
    assert memory["bytes"] == (123456 if available else None)
    assert memory["status"] == ("MEASURED" if available else "UNAVAILABLE")
    assert _PRIVATE not in json.dumps(memory)


def _canonical_content(payload):
    content = {k: v for k, v in payload.items() if k not in {"batch_number", "extracted_at"}}
    return json.dumps(
        content, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def test_breakdown_golden_bytes_and_direct_json_call_counts(monkeypatch):
    frame = pd.DataFrame({c: ["raw"] for c in CANONICAL_COLUMNS})
    frame["extra"] = pd.Series(["sample"], dtype=object)
    raw = SourceBatch(frame, "synthetic:SER-v1", 7, datetime(2026, 11, 8, tzinfo=timezone.utc))
    payload = encode_source_batch(raw)
    before = deepcopy(payload)
    canonical = _canonical_content(payload)
    golden = "34945b7ea9a48eae6d466d54af358b02611b4aac8807054d5ad375af2f71339d"
    sha = hashlib.sha256
    assert sha(canonical).hexdigest() == golden

    def require_golden_bytes(data):
        assert data == canonical
        return sha(data)

    monkeypatch.setattr(ser.hashlib, "sha256", require_golden_bytes)
    reconstructed, section = probe._fingerprint_breakdown(raw, payload)
    assert section["validated_content_sha256"] == golden and section["public_digests_match"]
    assert section["canonical_json"] == {
        "input_variant": "original",
        "public_invocations": 1,
        "by_direct_caller": {
            "_read_scalar": 2,  # Only extracted_at needs the fallback; nine cells match directly.
            "_read_index": 2,
            "_decode": 4,  # Index label and whole payload; nine post-dtype tokens match directly.
            "_fingerprint_validated_payload": 1,
            "other": 0,
        },
        "total_calls": 9,
    }
    assert payload == before == encode_source_batch(reconstructed)
    assert reconstructed.data is not raw.data


@pytest.mark.parametrize("damage", ["noncanonical_integer", "invalid_provenance"])
def test_matching_digest_never_admits_malformed_fingerprint_input(monkeypatch, damage):
    raw = _batch(pd.RangeIndex(1), [_DATE])
    payload = encode_source_batch(raw)
    original_hash = hashlib.sha256(_canonical_content(payload)).hexdigest()
    if damage == "noncanonical_integer":
        payload["records"][0]["values"][0] = {"type": "int", "value": "01"}
    else:
        payload["extracted_at"] = {"type": "none"}
    matching = hashlib.sha256(_canonical_content(payload)).hexdigest()
    assert (original_hash == matching) == (damage == "invalid_provenance")
    attempted_hashes = []

    def forged_digest(value):
        attempted_hashes.append(1)
        return matching

    monkeypatch.setattr(ser, "_fingerprint_validated_payload", forged_digest)
    with pytest.raises(SerializationError):
        probe._fingerprint_breakdown(raw, payload)
    assert not attempted_hashes


def test_fingerprint_diagnostic_preserves_sensitive_typed_source_and_payload():
    idx = pd.Index([Decimal("1.0"), Decimal("1.00"), Decimal("1.0")], dtype=object)
    raw = _batch(idx, [_DATE, None, _DATE])
    raw.data["nullable"] = pd.array([None, 0, 1], dtype="Int64")
    raw.data["clock"] = pd.array(
        [pd.Timestamp("2026-11-08 09:20:00.123456789", tz="Asia/Karachi"), pd.NaT, pd.NaT],
        dtype="datetime64[ns, Asia/Karachi]",
    )
    for values in ([0.0, -0.0, 0.0], ["\x00اردو", _PRIVATE, ""]):
        raw.data.insert(len(raw.data.columns), "repeat", values, allow_duplicates=True)
    payload = encode_source_batch(raw)
    before = deepcopy(payload)
    expected = hashlib.sha256(_canonical_content(before)).hexdigest()
    restored, section = probe._fingerprint_breakdown(raw, payload)
    assert encode_source_batch(restored) == encode_source_batch(raw) == payload == before
    assert section["validated_content_sha256"] == expected
    output = json.dumps(section, ensure_ascii=False)
    for private in (_PRIVATE, "اردو", "Asia/Karachi", "record_payload", '"records"'):
        assert private not in output


def test_fingerprint_windows_measure_uncached_actual_operations(monkeypatch):
    raw = _batch(pd.RangeIndex(2), [_DATE] * 2)
    payload = encode_source_batch(raw)
    clock = [0]
    public_inputs = []
    monkeypatch.setattr(probe.time, "perf_counter_ns", lambda: clock[0])
    monkeypatch.setattr(probe.time, "process_time_ns", lambda: clock[0])
    encode, decode = ser._encode, ser._decode
    digest, public = ser._fingerprint_validated_payload, ser.fingerprint_source_batch

    def timed_encode(batch):
        clock[0] += 2
        return encode(batch)

    def timed_decode(value):
        clock[0] += 3
        return decode(value)

    def timed_digest(value):
        clock[0] += 5
        return digest(value)

    def observed_public(batch):
        public_inputs.append(batch)
        return public(batch)

    monkeypatch.setattr(ser, "_encode", timed_encode)
    monkeypatch.setattr(ser, "_decode", timed_decode)
    monkeypatch.setattr(ser, "_fingerprint_validated_payload", timed_digest)
    monkeypatch.setattr(ser, "fingerprint_source_batch", observed_public)
    restored, section = probe._fingerprint_breakdown(raw, payload)
    assert sum(b is raw for b in public_inputs) == 7  # Warm-up, five timed, one counted.
    assert sum(b is restored for b in public_inputs) == 6
    durations = {
        "public_original": 12,
        "public_reconstructed": 12,
        "core_encode": 2,
        "strict_decode": 5,
        "canonical_digest": 5,
    }
    assert section["windows"].keys() == durations.keys()
    assert [section["windows"][name]["input_variant"] for name in durations] == [
        "original",
        "reconstructed",
        "original",
        "validated_payload",
        "validated_payload",
    ]
    for name, duration in durations.items():
        window = section["windows"][name]
        assert window["median_cpu_ns"] == window["median_wall_ns"] == duration
        assert window["observations"] == [
            {"run": run, "wall_ns": duration, "cpu_ns": duration} for run in range(1, 6)
        ]


def test_reconstructed_public_digest_mismatch_fails(monkeypatch):
    raw = _batch(pd.RangeIndex(1), [_DATE])
    public = ser.fingerprint_source_batch
    monkeypatch.setattr(
        ser, "fingerprint_source_batch", lambda batch: public(batch) if batch is raw else "0" * 64
    )
    with pytest.raises(probe._ProbeError, match="FINGERPRINT_MISMATCH"):
        probe._profile_batch(raw, fingerprint_breakdown=True)


def test_counter_cleanup_on_failure_and_existing_hook_protection(monkeypatch):
    raw = _batch(pd.RangeIndex(1), [_DATE])

    def fail(batch):
        assert sys.getprofile() is not None
        raise SerializationError("synthetic failure")

    monkeypatch.setattr(ser, "fingerprint_source_batch", fail)
    assert sys.getprofile() is None
    with pytest.raises(SerializationError, match="synthetic failure"):
        probe._count_fingerprint_json(raw, "0" * 64)
    assert sys.getprofile() is None

    def existing(frame, event, arg):
        pass

    sys.setprofile(existing)
    try:
        with pytest.raises(probe._ProbeError, match="PROFILE_ALREADY_ACTIVE"):
            probe._profile_batch(raw, fingerprint_breakdown=True)
        assert sys.getprofile() is existing
    finally:
        sys.setprofile(None)


def test_breakdown_cli_database_free_and_sanitized(tmp_path):
    path = tmp_path / f"{_PRIVATE}.xlsx"
    digest = _workbook(path, 1000, extra=True)
    result = _standalone(
        ["--path", path, "--expected-sha256", digest, "--rows", 1000, "--fingerprint-breakdown"],
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["database_operations"] == 0
    section = report["fingerprint_breakdown"]
    assert section["schema"] == "dataxai.fingerprint-breakdown-v1"
    assert section["rows"] == report["rows"] == 1000 and section["row_accounting_verified"]
    assert section["canonical_json"]["by_direct_caller"]["_fingerprint_validated_payload"] == 1
    assert len(report["components"]) == 4 and len(section["windows"]) == 5
    assert _PRIVATE not in result.stdout + result.stderr and "اردو" not in result.stdout
    assert list(tmp_path.iterdir()) == [path]


def test_breakdown_rejects_argument_value_without_echoing_it(capsys):
    with pytest.raises(SystemExit) as error:
        probe.main(
            [
                "--path",
                "unused",
                "--expected-sha256",
                "0" * 64,
                "--rows",
                "1000",
                f"--fingerprint-breakdown={_PRIVATE}",
            ]
        )
    assert error.value.code == 2
    output = capsys.readouterr()
    assert not output.out and _PRIVATE not in output.err
