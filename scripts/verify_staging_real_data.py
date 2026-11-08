"""Bounded 4.4.7 benchmark harness; durable writes require explicit authorization.

Provision/migrate a separate PostgreSQL database manually. Select it using the
existing POSTGRES_* environment and DJANGO_SETTINGS_MODULE=config.settings.dev.
Supply --expected-db/host/port; the runner never provisions or cleans databases.
The .env POSTGRES_DB and known application/source names are forbidden targets.
Use process-local environment overrides, not a rewritten .env, for the benchmark.

Resume rescans the immutable workbook and verifies original staged content. Its
source-reference manifest binds label, file digest, basename and batch size.
Only reconstructed batches are transformed. No whole-run transaction, scheduler,
heartbeat, automatic FAILED transition or ingestion finalization is provided.
One batch is materialized, often in several representations; not constant memory.

Optional --profile-mode metrics adds stage wall/CPU and aggregate SQL measurements.
The calls mode also observes an allowlist of existing functions with sys.setprofile;
it is substantially more expensive and intended for small diagnostic selections.
Neither mode is an uninstrumented throughput baseline. Nested measurements overlap.
Run this standalone, without other threads using Django settings or the connection.
Profiling suppresses debug-query capture temporarily and restores it on exit.
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
import time
import tracemalloc
from collections import Counter
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import asdict
from pathlib import Path
from uuid import UUID, uuid5

_repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_repo))
sys.path.insert(0, str(_repo / "apps"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

import django  # noqa: E402

django.setup()

from decouple import RepositoryEnv  # noqa: E402
from django.conf import settings  # noqa: E402
from django.db import DEFAULT_DB_ALIAS, DatabaseError, connections  # noqa: E402
from django.db.models import Count, Max, Min, Sum  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from etl.attempt_claims import claim_attempt  # noqa: E402
from etl.exceptions import (  # noqa: E402
    AttemptConflictError,
    AttemptIntegrityError,
    ETLError,
    StagingConflictError,
)
from etl.models import (  # noqa: E402
    StagingBatch,
    StagingIngestion,
    StagingRecord,
    TransformationAttempt,
    TransformationRecordResult,
)
from etl.serialization import fingerprint_source_batch  # noqa: E402
from etl.sources import ExcelSource  # noqa: E402
from etl.sources.contract import CANONICAL_COLUMNS  # noqa: E402
from etl.staging import stage_source_batch  # noqa: E402
from etl.staging_reader import load_source_batch  # noqa: E402
from etl.transformation_attempts import complete_attempt, register_attempt  # noqa: E402
from etl.transforms import AliasMapper, DateNormalizer  # noqa: E402
from etl.transforms.date_contract import SourceDateProfile  # noqa: E402

REPORT_SCHEMA = "dataxai.staging-benchmark-v1"
PROFILE_SCHEMA = "dataxai.staging-profile-v1"
_active_profile = ContextVar("staging_profile", default=None)
_STAGES = frozenset(
    {
        "extract",
        "stage",
        "reconstruct",
        "source_verification",
        "alias_mapping",
        "date_interpretation",
        "register",
        "claim",
        "complete_or_verify",
        "reconcile",
    }
)


class VerificationError(Exception):
    """Only controlled codes, never source values, cross the CLI boundary."""


def _require(condition, code):
    if not condition:
        raise VerificationError(code)


def _windows_peak_working_set():
    """Process-lifetime resident working-set high-water mark, not Python allocations."""
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(Counters),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(
        kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    ):
        raise OSError("PROCESS_MEMORY_UNAVAILABLE")
    return counters.PeakWorkingSetSize


def _process_memory():
    value, status = None, "UNSUPPORTED_PLATFORM"
    if sys.platform == "win32":
        try:
            value = _windows_peak_working_set()
            status = "MEASURED"
        except (OSError, AttributeError):
            status = "UNAVAILABLE"
    return {
        "peak_working_set_bytes": value,
        "status": status,
        "scope": "process lifetime, including work before profiling; not a per-batch peak",
    }


def _function_allowlist():
    # Code identities only: no wrappers, monkeypatches, locals, arguments or source strings.
    # These private observation points are diagnostic; tests guard compatibility.
    import psycopg
    from django.db.models.query import QuerySet

    from etl import serialization, staging, staging_reader, transformation_attempts
    from etl.models import EscapedJSONTextField

    functions = {
        serialization.encode_source_batch: "ser_encode_validated",
        serialization._encode: "ser_encode_core",
        serialization.decode_source_batch: "ser_decode_public",
        serialization._decode: "ser_decode_validate",
        serialization._json: "ser_canonical_json",
        serialization.fingerprint_source_batch: "ser_fingerprint",
        serialization._encode_and_fingerprint: "ser_encode_and_fingerprint",
        serialization._decode_and_fingerprint: "ser_decode_and_fingerprint",
        serialization._fingerprint_validated_payload: "ser_validated_payload_fingerprint",
        staging.stage_source_batch: "staging_service",
        staging_reader.load_source_batch: "read_reconstruct_verify",
        transformation_attempts._ledger: "ledger_build_validate",
        transformation_attempts._verify_ledger: "ledger_verify",
        transformation_attempts.complete_attempt: "outcome_finalize_or_verify",
        EscapedJSONTextField.from_db_value: "storage_json_decode_validate",
        EscapedJSONTextField.get_prep_value: "storage_json_encode",
        QuerySet.bulk_create: "bulk_insert",
    }
    for cursor in (psycopg.Cursor, psycopg.ServerCursor):
        for method in ("fetchone", "fetchmany", "fetchall"):
            functions[getattr(cursor, method)] = "postgres_fetch"
    return {function.__code__: label for function, label in functions.items()}


def _measurements():
    return {"stages": {}, "sql": {}, "functions": {}}


class _Profiler:
    """Fixed-key aggregates, plus O(allowlisted call depth) numeric stack entries.

    No per-query/per-call history or frame references are retained. Each batch has
    the same bounded metric tables as the execution total. SQL and function data
    are nested inside stage times; they are NOT independent additive costs.
    """

    def __init__(self, mode):
        self.mode = mode
        self.total = _measurements()
        self.batch = None
        self.stage_name = "outside_stages"
        self.stack = []
        self.codes = _function_allowlist() if mode == "calls" else {}
        self.wall_started = self.cpu_started = None
        self.elapsed = None

    def _tables(self, kind):
        yield self.total[kind]
        if self.batch is not None:
            yield self.batch[kind]

    def begin_batch(self):
        self.batch = _measurements()

    @contextmanager
    def stage(self, name):
        _require(name in _STAGES, "INVALID_PROFILE_STAGE")
        previous, self.stage_name = self.stage_name, name
        wall, cpu = time.perf_counter(), time.process_time()
        try:
            yield
        finally:
            wall, cpu = time.perf_counter() - wall, time.process_time() - cpu
            for table in self._tables("stages"):
                metric = table.setdefault(name, Counter())
                metric.update(calls=1, wall_seconds=wall, cpu_seconds=cpu)
            self.stage_name = previous

    def _execute(self, execute, sql, params, many, context):
        # Classify only a fixed set of SQL verbs; never retain SQL/params/context.
        match = (
            re.match(
                r"\s*(SELECT|INSERT|UPDATE|DELETE|SAVEPOINT|RELEASE|ROLLBACK)\b", sql, re.IGNORECASE
            )
            if isinstance(sql, str)
            else None
        )
        kind = match[1].upper() if match else "OTHER"
        wall, cpu, failed = time.perf_counter(), time.process_time(), True
        try:
            result = execute(sql, params, many, context)
            failed = False
            return result
        finally:
            wall, cpu = time.perf_counter() - wall, time.process_time() - cpu
            for table in self._tables("sql"):
                metric = table.setdefault(self.stage_name, {}).setdefault(kind, Counter())
                metric.update(
                    execute_calls=1,
                    executemany_calls=int(many),
                    failed_calls=int(failed),
                    wall_seconds=wall,
                    cpu_seconds=cpu,
                )

    def _observe(self, frame, event, arg):
        code = frame.f_code
        label = self.codes.get(code)
        token = id(frame)
        if event.startswith("c_"):
            # Observe only the fingerprint's direct UTF-8/SHA calls, not file hashing
            # or arbitrary C functions. Do not keep the bound callable (or its data).
            if label != "ser_validated_payload_fingerprint":
                return
            name = getattr(arg, "__name__", "")
            label = {"encode": "fingerprint_utf8", "hexdigest": "fingerprint_sha256"}.get(name)
            if arg is hashlib.sha256:
                label = "fingerprint_sha256"
            token = id(arg)
        if label is None:
            return
        if label == "bulk_insert":
            label = {
                "stage": "staging_bulk_insert",
                "complete_or_verify": "outcome_bulk_insert",
            }.get(self.stage_name, "bulk_insert")
        if event in ("call", "c_call"):
            self.stack.append([token, label, time.perf_counter(), time.process_time(), 0.0, 0.0])
        elif event in ("return", "c_return", "c_exception") and self.stack:
            _require(self.stack[-1][0] == token, "PROFILE_STACK_MISMATCH")
            _, label, wall, cpu, child_wall, child_cpu = self.stack.pop()
            wall, cpu = time.perf_counter() - wall, time.process_time() - cpu
            for table in self._tables("functions"):
                metric = table.setdefault(label, Counter())
                metric.update(
                    calls=1,
                    inclusive_wall_seconds=wall,
                    inclusive_cpu_seconds=cpu,
                    exclusive_observed_wall_seconds=max(0.0, wall - child_wall),
                    exclusive_observed_cpu_seconds=max(0.0, cpu - child_cpu),
                )
            if self.stack:
                self.stack[-1][4] += wall
                self.stack[-1][5] += cpu

    @contextmanager
    def activate(self):
        # Standalone/single-thread harness only. Do not replace an existing profiler.
        _require(self.mode != "calls" or sys.getprofile() is None, "PROFILE_ALREADY_ACTIVE")
        connection = connections[DEFAULT_DB_ALIAS]
        old_debug, old_cursor = settings.DEBUG, connection.force_debug_cursor
        token = _active_profile.set(self)
        self.wall_started, self.cpu_started = time.perf_counter(), time.process_time()
        try:
            settings.DEBUG = False
            connection.force_debug_cursor = False
            with connection.execute_wrapper(self._execute):
                if self.mode == "calls":
                    sys.setprofile(self._observe)
                yield
        finally:
            if self.mode == "calls":
                sys.setprofile(None)
            self.elapsed = {
                "wall_seconds": time.perf_counter() - self.wall_started,
                "cpu_seconds": time.process_time() - self.cpu_started,
            }
            self.stack.clear()
            _active_profile.reset(token)
            connection.force_debug_cursor = old_cursor
            settings.DEBUG = old_debug

    def snapshot(self):
        return {
            "schema": PROFILE_SCHEMA,
            "mode": self.mode,
            "debug_query_capture": "temporarily_disabled",
            "elapsed": self.elapsed
            or {
                "wall_seconds": time.perf_counter() - self.wall_started,
                "cpu_seconds": time.process_time() - self.cpu_started,
            },
            "totals": self.total,
            "process_memory": _process_memory(),
            "notes": {
                "overlap": "Stages, SQL and functions overlap. Never sum nested inclusive times.",
                "exclusive_observed": (
                    "Subtracts only allowlisted child calls, not all callees; not pure self time."
                ),
                "sql": (
                    "Django execute/executemany invocations, including failed calls; many counts "
                    "as one. Excludes driver-internal SQL, complete fetch/conversion and commits "
                    "outside execute_wrapper. Not pure PostgreSQL server time."
                ),
                "functions": (
                    "Allowlisted current-thread calls only in calls mode. Fetch includes driver "
                    "conversion; storage JSON conversion is separate but can nest other metrics. "
                    "Fingerprint includes validated encoding; SHA/UTF-8/JSON are nested subsets. "
                    "Bulk insert includes ORM/field preparation and SQL, not terminal commit."
                ),
                "overhead": (
                    "Instrumented diagnostics, not baseline throughput. Calls mode observes all "
                    "profile events but retains only fixed-key aggregates; overhead can be high. "
                    "CPU is process-wide. Use a fresh standalone process for memory comparisons."
                ),
            },
        }


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise VerificationError("INVALID_ARGUMENTS")


def _arguments(argv):
    parser = _Parser(description=__doc__)
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--mode", choices=("create", "resume"), required=True)
    parser.add_argument("--ingestion-id", type=UUID, required=True)
    parser.add_argument("--source-reference", required=True, help="Credential-free opaque label")
    parser.add_argument("--expected-db", required=True)
    parser.add_argument("--expected-host", required=True)
    parser.add_argument("--expected-port", type=int, required=True)
    parser.add_argument("--authorize-benchmark-writes", action="store_true")
    parser.add_argument("--batch-size", type=int, default=50_000)
    parser.add_argument("--max-batches", type=int)
    parser.add_argument("--expected-rows", type=int, default=1_067_371)
    parser.add_argument("--expected-batches", type=int, default=22)
    parser.add_argument("--claim-ttl", type=int, default=3600)
    parser.add_argument("--transformation-version", default="uci-dates-v1")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--trace-memory", action="store_true")
    parser.add_argument("--profile-mode", choices=("off", "metrics", "calls"), default="off")
    args = parser.parse_args(argv)
    _require(args.authorize_benchmark_writes, "WRITE_AUTHORIZATION_REQUIRED")
    _require(
        args.batch_size > 0
        and args.expected_rows > 0
        and args.expected_batches > 0
        and (args.max_batches is None or 1 <= args.max_batches <= args.expected_batches)
        and 1 <= args.claim_ttl <= 3600
        and 1 <= args.expected_port <= 65535,
        "INVALID_ARGUMENTS",
    )
    for value, limit in ((args.source_reference, 64), (args.transformation_version, 128)):
        _require(
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value) and len(value) <= limit,
            "INVALID_LABEL_OR_VERSION",
        )
    _require(args.path.is_file(), "INPUT_UNAVAILABLE")
    _require(
        args.expected_sha256 is None or re.fullmatch(r"[0-9a-fA-F]{64}", args.expected_sha256),
        "INVALID_DIGEST",
    )
    _require(
        args.report.suffix.lower() == ".json"
        and args.report.parent.is_dir()
        and args.report.resolve() != args.path.resolve(),
        "INVALID_REPORT_DESTINATION",
    )
    if args.report.exists():
        try:
            old = json.loads(args.report.read_text(encoding="utf-8"))
            _require(
                old["schema"] == REPORT_SCHEMA and old["ingestion_id"] == str(args.ingestion_id),
                "REPORT_IDENTITY_CONFLICT",
            )
        except (ValueError, KeyError, TypeError):
            raise VerificationError("INVALID_EXISTING_REPORT") from None
    return args


def _database_guard(args):
    connection = connections[DEFAULT_DB_ALIAS]
    _require(connection.vendor == "postgresql", "POSTGRES_REQUIRED")
    _require(
        not connection.in_atomic_block and connection.get_autocommit(),
        "OUTER_TRANSACTION_FORBIDDEN",
    )
    forbidden = {"dataxai", "dataxai_source", "postgres", "template0", "template1"}
    env_path = _repo / ".env"
    if env_path.is_file():
        forbidden.add(RepositoryEnv(str(env_path)).data.get("POSTGRES_DB", "dataxai"))
    forbidden.add(make_url(settings.SOURCE_DATABASE_URL).database)
    _require(args.expected_db not in forbidden, "FORBIDDEN_DATABASE")
    configured = connection.settings_dict
    _require(
        (str(configured["NAME"]), str(configured["HOST"]), str(configured["PORT"]))
        == (args.expected_db, args.expected_host, str(args.expected_port)),
        "DATABASE_IDENTITY_MISMATCH",
    )
    connection.ensure_connection()
    info = connection.connection.info
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database()")
        actual_name = cursor.fetchone()[0]
    _require(
        (actual_name, info.host, info.port)
        == (args.expected_db, args.expected_host, args.expected_port),
        "DATABASE_IDENTITY_MISMATCH",
    )


def _database_size():
    # A whole-database measurement, not exact storage consumed by this ingestion.
    try:
        with connections[DEFAULT_DB_ALIAS].cursor() as cursor:
            cursor.execute("SELECT pg_database_size(current_database())")
            return cursor.fetchone()[0]
    except DatabaseError:
        return None


def _dataset(path):
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    after = path.stat()
    _require(
        (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns), "INPUT_CHANGED"
    )
    return {
        "sha256": digest.hexdigest(),
        "size_bytes": after.st_size,
        "mtime_ns": after.st_mtime_ns,
    }


def _source_reference(args, dataset):
    name_hash = hashlib.sha256(args.path.name.encode("utf-8")).hexdigest()[:16]
    return f"{args.source_reference}.sha256-{dataset['sha256']}.b{args.batch_size}.n{name_hash}"


def _ingestion(args, reference):
    existing = StagingIngestion.objects.filter(pk=args.ingestion_id).first()
    if args.mode == "create":
        _require(existing is None, "INGESTION_ALREADY_EXISTS")
        # Race uniqueness errors fail closed at the CLI boundary; no overwrite/upsert.
        StagingIngestion.objects.create(
            id=args.ingestion_id, source_kind="EXCEL", source_reference=reference
        )
    else:
        _require(existing is not None, "INGESTION_NOT_FOUND")
        _require(
            existing.source_kind == "EXCEL"
            and existing.source_reference == reference
            and existing.status == "OPEN",
            "INGESTION_IDENTITY_CONFLICT",
        )


def attempt_key(ingestion_id, batch_number, version):
    """Stable UUIDv5 identity; configuration compatibility is checked by registration."""
    return uuid5(ingestion_id, f"staging-benchmark-v1:{batch_number}:{version}")


def _source_check(raw, staged, restored):
    _require(
        tuple(raw.data.columns) == tuple(restored.data.columns) == CANONICAL_COLUMNS,
        "SOURCE_SCHEMA_MISMATCH",
    )
    # SER-v1 hashing includes exact typed values, column/index metadata and order.
    # Batch identity is checked separately; fresh extraction time is deliberately excluded.
    _require(
        raw.batch_number == restored.batch_number == staged.batch_number
        and raw.source_name == restored.source_name == staged.source_name,
        "SOURCE_IDENTITY_MISMATCH",
    )
    _require(
        fingerprint_source_batch(raw) == staged.content_hash == fingerprint_source_batch(restored),
        "SOURCE_FINGERPRINT_MISMATCH",
    )
    records = StagingRecord.objects.filter(batch=staged)
    stats = records.aggregate(
        count=Count("pk"), first=Min("row_position"), last=Max("row_position")
    )
    count = len(raw)
    _require(
        count == len(restored) == staged.row_count == stats["count"]
        and (count == 0 or (stats["first"] == 0 and stats["last"] == count - 1)),
        "SOURCE_ACCOUNTING_MISMATCH",
    )


def _reconcile(staged, attempt, result):
    result.check_accounting()
    attempt.refresh_from_db()
    _require(
        attempt.status == "SUCCEEDED"
        and attempt.batch_id == staged.pk
        and (attempt.safe_row_count, attempt.unresolved_row_count)
        == (len(result.safe_positions), len(result.unresolved_positions)),
        "OUTCOME_ACCOUNTING_MISMATCH",
    )
    rows = TransformationRecordResult.objects.filter(attempt=attempt)
    stats = rows.aggregate(count=Count("pk"), unique=Count("staged_record_id", distinct=True))
    _require(
        stats["count"] == stats["unique"] == staged.row_count == result.input_rows
        and not rows.exclude(batch_id=staged.pk).exists()
        and not rows.exclude(staged_record__batch_id=staged.pk).exists()
        and not rows.filter(result_payload__isnull=False).exists(),
        "OUTCOME_ACCOUNTING_MISMATCH",
    )
    issues = {issue.row.position: issue.code.value for issue in result.issues}
    actual = (
        rows.order_by("staged_record__row_position")
        .values_list("staged_record__row_position", "outcome", "interpretation_evidence")
        .iterator(chunk_size=1000)
    )
    for position, row in enumerate(actual):
        evidence = result.date_evidence[position]
        code = issues.get(position)
        _require(
            row
            == (
                position,
                "UNRESOLVED" if code else "SAFE",
                {
                    "schema": "date-outcome-v1",
                    "timezone_status": evidence.timezone_status.value,
                    "timezone_origin": evidence.timezone_origin,
                    "issue_code": code,
                },
            ),
            "OUTCOME_EVIDENCE_MISMATCH",
        )
    return {
        "source_rows": result.input_rows,
        "staged_rows": staged.row_count,
        "reconstructed_rows": result.input_rows,
        "outcome_rows": stats["count"],
        "safe_rows": len(result.safe_positions),
        "unresolved_rows": len(result.unresolved_positions),
    }


def _write_report(path, report):
    profiler = _active_profile.get()
    if profiler is not None:
        report["profiling"] = profiler.snapshot()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".staging-report-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(report, stream, ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _timed(durations, name, operation):
    started = time.perf_counter()
    try:
        profiler = _active_profile.get()
        if profiler is not None:
            with profiler.stage(name):
                return operation()
        return operation()
    finally:
        durations[name] = time.perf_counter() - started


def _process(args, raw, item):
    durations = item["seconds"]
    _require(tuple(raw.data.columns) == CANONICAL_COLUMNS, "SOURCE_SCHEMA_MISMATCH")
    prior = StagingBatch.objects.filter(
        ingestion_id=args.ingestion_id, batch_number=raw.batch_number
    ).exists()
    staged = _timed(
        durations, "stage", lambda: stage_source_batch(ingestion_id=args.ingestion_id, batch=raw)
    )
    item["batch_disposition"] = "verified_reused" if prior else "new"
    restored = _timed(
        durations,
        "reconstruct",
        lambda: load_source_batch(ingestion_id=args.ingestion_id, batch_number=raw.batch_number),
    )
    _timed(durations, "source_verification", lambda: _source_check(raw, staged, restored))
    mapped = _timed(durations, "alias_mapping", lambda: AliasMapper().transform(restored))
    result = _timed(
        durations, "date_interpretation", lambda: DateNormalizer(SourceDateProfile()).apply(mapped)
    )
    key = attempt_key(args.ingestion_id, raw.batch_number, args.transformation_version)
    existed = TransformationAttempt.objects.filter(batch=staged, attempt_key=key).exists()
    attempt = _timed(
        durations,
        "register",
        lambda: register_attempt(
            ingestion_id=args.ingestion_id,
            batch_number=raw.batch_number,
            attempt_key=key,
            profile=SourceDateProfile(),
            transformation_version=args.transformation_version,
        ),
    )
    item["attempt_initial_status"] = attempt.status
    _require(attempt.status != "FAILED", "TERMINAL_FAILED_ATTEMPT")
    _require(
        not (attempt.status == "RUNNING" and attempt.claim_generation == 0),
        "LEGACY_RUNNING_ATTEMPT",
    )
    token = None
    if attempt.status != "SUCCEEDED":
        # The service, not worker time, decides active versus expired ownership.
        token = _timed(
            durations,
            "claim",
            lambda: claim_attempt(attempt_id=attempt.pk, ttl_seconds=args.claim_ttl),
        )
    completed = _timed(
        durations,
        "complete_or_verify",
        lambda: complete_attempt(
            attempt_id=attempt.pk,
            result=result,
            transformation_version=args.transformation_version,
            claim=token,
        ),
    )
    item["counts"] = _timed(durations, "reconcile", lambda: _reconcile(staged, completed, result))
    item["attempt_disposition"] = (
        "verified_reused" if attempt.status == "SUCCEEDED" else ("resumed" if existed else "new")
    )
    item["timezone_statuses"] = dict(Counter(e.timezone_status.value for e in result.date_evidence))
    item["issues"] = dict(Counter(issue.code.value for issue in result.issues))
    item["status"] = "VERIFIED"


def main(argv=None):
    """Return 0 for a verified selected scope; 1 for execution failure, 2 for bad arguments.

    Reports never contain exception messages or raw values. Dependency logging is
    suppressed during this standalone benchmark to avoid SQL/token/path leakage.
    Kill/power loss may leave an INCOMPLETE report; resume uses DB services, not that
    report, as authority. Input files must remain immutable throughout execution.
    """
    try:
        args = _arguments(argv)
    except (VerificationError, OSError):
        print("FAIL: INVALID_ARGUMENTS_OR_AUTHORIZATION")
        return 2
    report = {
        "schema": REPORT_SCHEMA,
        "mode": args.mode,
        "status": "INCOMPLETE",
        "complete_dataset_verified": False,
        "failure_code": None,
        "ingestion_id": str(args.ingestion_id),
        "dataset_label": args.source_reference,
        "transformation_version": args.transformation_version,
        "profile": asdict(SourceDateProfile()),
        "batch_size": args.batch_size,
        "max_batches": args.max_batches,
        "expected": {"rows": args.expected_rows, "batches": args.expected_batches},
        "disposition_note": (
            "New/reused observations assume a single benchmark " "runner per ingestion."
        ),
        "counts_scope": (
            "Fully reconciled batches in this execution only; an incomplete or failed batch "
            "may already have committed staging records or an attempt. "
            "Resume verifies the database."
        ),
        "counts": {},
        "batches": [],
        "python_traced_peak_bytes": None,
        "process_peak_rss_bytes": None,
        "process_peak_rss_note": "not measured",
        "database_size_scope": "whole database; not exact staging-table storage",
    }
    started = time.perf_counter()
    old_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    tracing = args.trace_memory and not tracemalloc.is_tracing()
    if tracing:
        tracemalloc.start()
    database_checked = False
    instrumentation = ExitStack()
    profiler = None
    try:
        if args.profile_mode != "off":
            profiler = _Profiler(args.profile_mode)
            instrumentation.enter_context(profiler.activate())
        _write_report(args.report, report)  # Ensure reporting works before any DB mutation.
        _database_guard(args)
        database_checked = True
        report["database_size_before_bytes"] = _database_size()
        dataset = _dataset(args.path)
        report["dataset"] = dataset
        _require(
            args.expected_sha256 is None or dataset["sha256"] == args.expected_sha256.lower(),
            "DATASET_DIGEST_MISMATCH",
        )
        reference = _source_reference(args, dataset)
        _require(len(reference) <= 255, "INVALID_SOURCE_REFERENCE")
        totals = Counter()
        with ExcelSource(args.path, batch_size=args.batch_size) as source:
            report["sheet_count"] = len(source.sheet_names)
            _require(len(source.sheet_names) == 2, "WORKBOOK_SHEETS_MISMATCH")
            _ingestion(args, reference)
            iterator = iter(source.extract())
            while args.max_batches is None or len(report["batches"]) < args.max_batches:
                if profiler is not None:
                    profiler.begin_batch()
                extraction = time.perf_counter()
                raw = _timed({}, "extract", lambda: next(iterator, None))
                if raw is None:
                    break
                item = {
                    "batch_number": raw.batch_number,
                    "status": "INCOMPLETE",
                    "seconds": {"extract": time.perf_counter() - extraction},
                }
                if profiler is not None:
                    item["profiling"] = profiler.batch
                report["batches"].append(item)
                _require(raw.batch_number == len(report["batches"]) - 1, "BATCH_SEQUENCE_MISMATCH")
                _process(args, raw, item)
                totals.update(item["counts"])
                report["counts"] = dict(totals, batches=len(report["batches"]))
                _write_report(args.report, report)
                del raw
        stat = args.path.stat()
        _require(
            (stat.st_size, stat.st_mtime_ns) == (dataset["size_bytes"], dataset["mtime_ns"]),
            "INPUT_CHANGED",
        )
        _require(bool(report["batches"]), "EMPTY_SELECTION")
        if args.max_batches is not None:
            _require(len(report["batches"]) == args.max_batches, "PILOT_ACCOUNTING_MISMATCH")
            report["status"] = "PILOT_PASSED"
        else:
            stats = StagingBatch.objects.filter(ingestion_id=args.ingestion_id).aggregate(
                count=Count("pk"), rows=Sum("row_count")
            )
            _require(
                stats == {"count": args.expected_batches, "rows": args.expected_rows}
                and len(report["batches"]) == args.expected_batches
                and all(
                    totals[k] == args.expected_rows
                    for k in ("source_rows", "staged_rows", "reconstructed_rows", "outcome_rows")
                )
                and totals["safe_rows"] + totals["unresolved_rows"] == args.expected_rows,
                "FULL_ACCOUNTING_MISMATCH",
            )
            report["complete_dataset_verified"] = True
            report["status"] = "PASSED"
    except KeyboardInterrupt:
        report["status"], report["failure_code"] = "FAILED", "INTERRUPTED"
    except Exception as exc:
        # CLI error boundary: fail nonzero without exposing source/SQL/credential messages.
        code = "EXECUTION_ERROR"
        if isinstance(exc, VerificationError):
            code = str(exc)
        elif isinstance(exc, StagingConflictError):
            code = "SOURCE_CONTENT_CONFLICT"
        elif isinstance(exc, AttemptConflictError):
            code = "ATTEMPT_OR_LEASE_CONFLICT"
        elif isinstance(exc, AttemptIntegrityError):
            code = "ATTEMPT_INTEGRITY_ERROR"
        elif isinstance(exc, ETLError):
            code = "ETL_VERIFICATION_ERROR"
        report["status"], report["failure_code"] = "FAILED", code
    finally:
        if profiler is not None:
            profiler.batch = None
        report["elapsed_seconds"] = time.perf_counter() - started
        report["verified_rows_per_second"] = (
            report["counts"].get("source_rows", 0) / report["elapsed_seconds"]
        )
        report["throughput_denominator"] = (
            "overall wall time including file hash and verification; numerator reconciled rows"
        )
        report["dispositions"] = dict(
            Counter(
                f"{kind}_{batch[kind + '_disposition']}"
                for batch in report["batches"]
                for kind in ("batch", "attempt")
                if kind + "_disposition" in batch
            )
        )
        if tracing:
            report["python_traced_peak_bytes"] = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()
        if database_checked:
            try:
                report["database_size_after_bytes"] = _database_size()
                before_size, after_size = (
                    report["database_size_before_bytes"],
                    report["database_size_after_bytes"],
                )
                report["database_size_delta_bytes"] = (
                    None if before_size is None or after_size is None else after_size - before_size
                )
            except Exception:
                report["database_size_after_bytes"] = None
        instrumentation.close()
        if profiler is not None and profiler.elapsed is not None:
            report["profiling"] = profiler.snapshot()
            memory = report["profiling"]["process_memory"]
            report["process_peak_rss_bytes"] = memory["peak_working_set_bytes"]
            report["process_peak_rss_note"] = (
                "Windows peak working set; " + memory["scope"] + "; " + memory["status"]
            )
        logging.disable(old_logging)
    if report["status"] == "FAILED":
        report["complete_dataset_verified"] = False
        if report["batches"] and report["batches"][-1]["status"] == "INCOMPLETE":
            report["batches"][-1].update(status="FAILED", failure_code=report["failure_code"])
    try:
        _write_report(args.report, report)
    except OSError:
        print("FAIL: REPORT_WRITE_FAILED")
        return 1
    ok = report["status"] in ("PASSED", "PILOT_PASSED")
    print("PASS: " + report["status"] if ok else "FAIL: " + report["failure_code"])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
