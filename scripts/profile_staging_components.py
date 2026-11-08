"""Profile Gate 3 Python operations on the first complete ExcelSource batch, without a DB.

Run as a standalone process. JSON goes to stdout and the human summary to stderr.
Each observation sums disjoint windows over the same workload. Each group/selection
has one excluded warm-up followed by five measured repetitions. Only the operation
and its result allocation are timed. Preparation of inputs, correctness checks,
discarding results, and reporting are outside those windows.
"""

import argparse
import hashlib
import json
import logging
import os
import re
import statistics
import sys
import time
import warnings
from contextlib import contextmanager
from pathlib import Path

_GROUP_SIZE = 1000
_RUNS = 5
_COMPONENTS = {
    "construct_records": "Construct one group of unsaved StagingRecord objects with batch_id=1.",
    "prepare_text": "Call record_payload.get_prep_value on each SER-v1 record in the group.",
    "convert_text": "Call record_payload.from_db_value on prepared text, including revalidation.",
    "select_split_indexes": "Select source.data.iloc[list(positions)].index for each split.",
}
_FINGERPRINT_WINDOWS = {
    "public_original": ("original", "Complete fingerprint_source_batch(original)."),
    "public_reconstructed": ("reconstructed", "Complete fingerprint_source_batch(reconstructed)."),
    "core_encode": ("original", "_encode(original), without its public wrapper."),
    "strict_decode": ("validated_payload", "Strict _decode, including its verifying re-encode."),
    "canonical_digest": ("validated_payload", "Canonical JSON, UTF-8 and SHA-256."),
}


class _ProbeError(Exception):
    """Only fixed, non-sensitive error codes may cross the CLI boundary."""


def _setup_django():
    import django
    from django.conf import settings

    if settings.configured:
        raise _ProbeError("STANDALONE_PROCESS_REQUIRED")
    # Never load project settings, credentials, database routers, or a usable backend.
    settings.configure(
        DATABASES={"default": {"ENGINE": "django.db.backends.dummy"}},
        INSTALLED_APPS=(
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "accounts",
            "core",
            "etl",
        ),
        AUTH_USER_MODEL="accounts.User",
        LOGGING_CONFIG=None,
    )
    django.setup()


def _first_batch(path, rows, expected_sha256):
    from etl.sources import ExcelSource

    before = path.stat()
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != expected_sha256:
        raise _ProbeError("WORKBOOK_HASH_MISMATCH")
    with ExcelSource(path, batch_size=rows) as source:
        batch = next(source.extract(), None)
    after = path.stat()
    if not os.path.samestat(before, after) or (before.st_size, before.st_mtime_ns) != (
        after.st_size,
        after.st_mtime_ns,
    ):
        raise _ProbeError("WORKBOOK_CHANGED")
    if batch is None or batch.batch_number != 0 or len(batch) != rows:
        raise _ProbeError("INCOMPLETE_FIRST_BATCH")
    return batch


@contextmanager
def _time_window(sample):
    wall = time.perf_counter_ns()
    cpu = time.process_time_ns()
    yield
    sample["cpu_ns"] += time.process_time_ns() - cpu
    sample["wall_ns"] += time.perf_counter_ns() - wall


def _measure_records(records, runs):
    from etl.models import StagingRecord
    from etl.serialization import _json

    field = StagingRecord._meta.get_field("record_payload")
    groups = 0
    for start in range(0, len(records), _GROUP_SIZE):
        groups += 1
        group = records[start : start + _GROUP_SIZE]
        if any(record["row_position"] != pos for pos, record in enumerate(group, start)):
            raise _ProbeError("RECORD_POSITION_MISMATCH")
        # Immutable conversion inputs, prepared once per group outside all timers.
        texts = [field.get_prep_value(record) for record in group]
        if any(not text.isascii() or "\x00" in text for text in texts):
            raise _ProbeError("INVALID_STORAGE_ENVELOPE")
        for run in runs:
            with _time_window(run["construct_records"]):
                objects = [
                    StagingRecord(batch_id=1, row_position=row["row_position"], record_payload=row)
                    for row in group
                ]
            if any(
                obj.pk is not None
                or obj.batch_id != 1
                or obj.row_position != row["row_position"]
                or obj.record_payload is not row
                for obj, row in zip(objects, group, strict=True)
            ):
                raise _ProbeError("UNSAVED_RECORD_MISMATCH")
            del objects

            with _time_window(run["prepare_text"]):
                prepared = [field.get_prep_value(record) for record in group]
            if prepared != texts:
                raise _ProbeError("INCONSISTENT_PREPARATION")
            del prepared

            with _time_window(run["convert_text"]):
                decoded = [field.from_db_value(text, None, None) for text in texts]
            # SER-v1's canonical comparison also distinguishes JSON integers and booleans.
            if any(
                _json(restored) != _json(record)
                for restored, record in zip(decoded, group, strict=True)
            ):
                raise _ProbeError("RECORD_ROUNDTRIP_MISMATCH")
            del decoded
    return groups


def _interpret_splits(source):
    from etl.serialization import _index
    from etl.transforms import AliasMapper, DateNormalizer, SourceDateProfile

    result = DateNormalizer(SourceDateProfile()).apply(AliasMapper().transform(source))
    result.check_accounting()
    if result.input_rows != len(source):
        raise _ProbeError("INTERPRETATION_COUNT_MISMATCH")
    return (
        (result.safe_positions, _index(result.safe_batch.data.index)),
        (result.unresolved_positions, _index(result.unresolved_batch.data.index)),
    )


def _summary(runs, component, operations, description=None):
    observations = [
        {"run": number, **run[component]} for number, run in enumerate(runs[1:], start=1)
    ]
    return {
        "timed_operation": description or _COMPONENTS[component],
        "operations_per_run": operations,
        "observations": observations,
        "total_wall_ns": sum(o["wall_ns"] for o in observations),
        "total_cpu_ns": sum(o["cpu_ns"] for o in observations),
        "median_wall_ns": statistics.median(o["wall_ns"] for o in observations),
        "median_cpu_ns": statistics.median(o["cpu_ns"] for o in observations),
    }


def _count_fingerprint_json(raw, expected):
    """Observe one untimed public call. Keep only fixed counters, never frames or arguments."""
    from etl import serialization as ser

    callers = {
        ser._read_scalar.__code__: "_read_scalar",
        ser._read_index.__code__: "_read_index",
        ser._decode.__code__: "_decode",
        ser._fingerprint_validated_payload.__code__: "_fingerprint_validated_payload",
    }
    json_code = ser._json.__code__
    counts = dict.fromkeys((*callers.values(), "other"), 0)

    def observe(frame, event, arg):
        if event == "call" and frame.f_code is json_code:
            counts[callers.get(frame.f_back.f_code, "other")] += 1

    previous = sys.getprofile()
    if previous is not None:
        raise _ProbeError("PROFILE_ALREADY_ACTIVE")
    try:
        sys.setprofile(observe)
        actual = ser.fingerprint_source_batch(raw)
    finally:
        sys.setprofile(previous)
    if actual != expected:
        raise _ProbeError("FINGERPRINT_MISMATCH")
    return {
        "input_variant": "original",
        "public_invocations": 1,
        "by_direct_caller": counts,
        "total_calls": sum(counts.values()),
    }


def _fingerprint_breakdown(raw, payload):
    from etl import serialization as ser

    if sys.getprofile() is not None:
        raise _ProbeError("PROFILE_ALREADY_ACTIVE")
    # This Gate 3 operation strictly decodes before computing the reference digest.
    source, expected = ser._decode_and_fingerprint(payload)
    if not len(raw) == len(source) == payload["row_count"] == len(payload["records"]):
        raise _ProbeError("FINGERPRINT_ROW_COUNT_MISMATCH")
    runs = [
        {name: {"wall_ns": 0, "cpu_ns": 0} for name in _FINGERPRINT_WINDOWS}
        for _ in range(1 + _RUNS)
    ]
    for run in runs:
        for name, batch in (("public_original", raw), ("public_reconstructed", source)):
            with _time_window(run[name]):
                digest = ser.fingerprint_source_batch(batch)
            if digest != expected:
                raise _ProbeError("FINGERPRINT_MISMATCH")

        with _time_window(run["core_encode"]):
            encoded = ser._encode(raw)
        if ser._json(encoded) != ser._json(payload):
            raise _ProbeError("FINGERPRINT_ENCODING_MISMATCH")
        del encoded

        with _time_window(run["strict_decode"]):
            decoded = ser._decode(payload)
        if len(decoded) != len(raw):
            raise _ProbeError("FINGERPRINT_ROW_COUNT_MISMATCH")
        del decoded

        with _time_window(run["canonical_digest"]):
            digest = ser._fingerprint_validated_payload(payload)
        if digest != expected:
            raise _ProbeError("FINGERPRINT_MISMATCH")

    return source, {
        "schema": "dataxai.fingerprint-breakdown-v1",
        "rows": len(raw),
        "validated_content_sha256": expected,
        "public_digests_match": True,
        "row_accounting_verified": True,
        "warmup_repetitions": 1,
        "measured_repetitions": _RUNS,
        "timing_scope": (
            "Public timings are inclusive. Strict decode includes a core encode and canonical "
            "comparisons. Internal windows are independent observations, not additive parts "
            "of a public timing. Checks, setup and the separate call-count pass are excluded."
        ),
        "windows": {
            name: {"input_variant": variant, **_summary(runs, name, 1, description)}
            for name, (variant, description) in _FINGERPRINT_WINDOWS.items()
        },
        "canonical_json": _count_fingerprint_json(raw, expected),
    }


def _profile_batch(raw, *, fingerprint_breakdown=False):
    from etl.serialization import _index, decode_source_batch, encode_source_batch

    if not 0 < len(raw) <= 5000:
        raise _ProbeError("INVALID_BATCH_SIZE")
    payload = encode_source_batch(raw)
    extra = {}
    if fingerprint_breakdown:
        source, extra["fingerprint_breakdown"] = _fingerprint_breakdown(raw, payload)
        del raw
    else:
        del raw
        source = decode_source_batch(payload)
    records = payload["records"]
    del payload
    splits = _interpret_splits(source)
    runs = [
        {component: {"wall_ns": 0, "cpu_ns": 0} for component in _COMPONENTS}
        for _ in range(1 + _RUNS)
    ]
    groups = _measure_records(records, runs)
    del records
    for positions, expected_index in splits:
        for run in runs:
            with _time_window(run["select_split_indexes"]):
                selected = source.data.iloc[list(positions)].index
            if _index(selected) != expected_index:
                raise _ProbeError("INDEX_REPRESENTATION_MISMATCH")
            del selected
    rows = len(source)
    return {
        "rows": rows,
        "safe_rows": len(splits[0][0]),
        "unresolved_rows": len(splits[1][0]),
        "record_groups": groups,
        "max_rows_per_group": _GROUP_SIZE,
        "warmup_repetitions": 1,
        "measured_repetitions": _RUNS,
        "components": {
            name: _summary(runs, name, len(splits) if name == "select_split_indexes" else rows)
            for name in _COMPONENTS
        },
        **extra,
    }


def _windows_peak_bytes():
    import ctypes
    from ctypes import wintypes

    # PROCESS_MEMORY_COUNTERS: two DWORDs followed by eight pointer-sized counters.
    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("remaining_sizes", ctypes.c_size_t * 7),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    measure = kernel.K32GetProcessMemoryInfo
    measure.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    measure.restype = wintypes.BOOL
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    if not measure(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise OSError("PROCESS_MEMORY_UNAVAILABLE")
    return counters.PeakWorkingSetSize


def _process_memory():
    metric, value, status = None, None, "UNSUPPORTED_PLATFORM"
    try:
        if sys.platform == "win32":
            metric = "peak_working_set"
            value = _windows_peak_bytes()
        elif sys.platform in {"linux", "darwin"}:
            import resource

            metric = "peak_resident_set"
            value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            if sys.platform == "linux":
                value *= 1024  # Linux reports KiB; Darwin reports bytes.
        if value is not None:
            status = "MEASURED"
    except (OSError, AttributeError, ImportError):
        status = "UNAVAILABLE"
    return {
        "metric": metric,
        "bytes": value,
        "status": status,
        "scope": "process-lifetime high-water mark including setup; not component allocations",
    }


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "Invalid profiling arguments. See --help.\n")


def _sanitized_warning(*args, **kwargs):
    # Workbook/third-party warning text can include source values or file paths.
    print("Profiling warning (details withheld).", file=sys.stderr)


def main(argv=None):
    parser = _Parser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--rows", type=int, choices=(1000, 5000), required=True)
    parser.add_argument(
        "--fingerprint-breakdown",
        action="store_true",
        help="Also measure public fingerprints, codec operations and untimed canonical JSON calls.",
    )
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[0-9a-fA-F]{64}", args.expected_sha256):
        parser.error("Invalid SHA-256")

    old_logging = logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        digest = args.expected_sha256.lower()
        with warnings.catch_warnings():
            warnings.showwarning = _sanitized_warning
            _setup_django()
            report = _profile_batch(
                _first_batch(args.path, args.rows, digest),
                fingerprint_breakdown=args.fingerprint_breakdown,
            )
        report.update(
            schema="dataxai.staging-components-v1",
            requested_rows=args.rows,
            dataset_sha256=digest,
            database_operations=0,
            timing_scope=(
                "Per-repetition sums of non-overlapping group/selection windows; "
                "first repetition of each excluded. Setup, checks and reporting excluded. "
                "Conversion includes the field's internal revalidation."
            ),
            process_memory=_process_memory(),
        )
    except _ProbeError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1
    except Exception:
        # Only the standalone CLI sanitizes unexpected errors. Fail without partial evidence.
        print("FAIL COMPONENT_PROFILE_FAILED", file=sys.stderr)
        return 1
    finally:
        logging.disable(old_logging)

    print(json.dumps(report, allow_nan=False, indent=2))
    print(
        f"PASS {report['rows']} rows, {report['record_groups']} groups, 0 database operations",
        file=sys.stderr,
    )
    timings = dict(report["components"])
    if args.fingerprint_breakdown:
        timings.update(report["fingerprint_breakdown"]["windows"])
    for name, component in timings.items():
        print(
            f"{name}: median wall {component['median_wall_ns'] / 1e6:.3f} ms, "
            f"CPU {component['median_cpu_ns'] / 1e6:.3f} ms",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps"))
    raise SystemExit(main())
