"""Small UCI fixture, real disposable PostgreSQL, no million-row benchmark."""

import importlib.util
import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from django.conf import settings
from django.db import DatabaseError, connections
from django.test.utils import CaptureQueriesContext

from etl import transformation_attempts
from etl.attempt_claims import claim_attempt
from etl.exceptions import StagingIntegrityError
from etl.models import (
    StagingBatch,
    StagingIngestion,
    StagingRecord,
    TransformationAttempt,
    TransformationRecordResult,
)
from etl.tests.test_staging_constraints import staging_postgres as staging_postgres
from etl.tests.test_staging_persistence import _corrupt_with_matching_digest
from etl.tests.test_staging_persistence import application_postgres as application_postgres
from etl.transformation_attempts import fail_attempt, start_attempt
from etl.transforms.date_contract import SourceDateProfile

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = Path(__file__).parent / "fixtures" / "online_retail_ii_sample.xlsx"


@pytest.fixture
def runner():
    spec = importlib.util.spec_from_file_location(
        "staging_verifier", ROOT / "scripts" / "verify_staging_real_data.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def options(tmp_path):
    settings = connections["default"].settings_dict
    return [
        "--path",
        str(FIXTURE),
        "--mode",
        "create",
        "--ingestion-id",
        str(uuid4()),
        "--source-reference",
        "synthetic-uci-fixture",
        "--expected-db",
        settings["NAME"],
        "--expected-host",
        settings["HOST"],
        "--expected-port",
        str(settings["PORT"]),
        "--authorize-benchmark-writes",
        "--expected-rows",
        "18",
        "--expected-batches",
        "2",
        "--report",
        str(tmp_path / "report.json"),
    ]


def _set(options, key, value):
    changed = list(options)
    if key in changed:
        changed[changed.index(key) + 1] = str(value)
    else:
        changed.extend([key, str(value)])
    return changed


def _report(options):
    return json.loads(Path(options[options.index("--report") + 1]).read_text(encoding="utf-8"))


def _snapshot():
    return tuple(
        list(model.objects.order_by("pk").values())
        for model in (
            StagingIngestion,
            StagingBatch,
            StagingRecord,
            TransformationAttempt,
            TransformationRecordResult,
        )
    )


def _no_writes(queries):
    assert not any(
        q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries
    )


def test_fixture_full_and_identical_resume_use_original_metadata(runner, options, monkeypatch):
    assert runner.main(options) == 0
    report = _report(options)
    assert report["status"] == "PASSED" and report["complete_dataset_verified"] is True
    assert report["counts"] == {
        "batches": 2,
        "source_rows": 18,
        "staged_rows": 18,
        "reconstructed_rows": 18,
        "outcome_rows": 18,
        "safe_rows": 18,
        "unresolved_rows": 0,
    }
    assert report["sheet_count"] == 2
    assert sum(item["timezone_statuses"]["TIMEZONE_UNKNOWN"] for item in report["batches"]) == 18
    assert report["profile"]["timezone"] is None
    assert report["dispositions"] == {"batch_new": 2, "attempt_new": 2}
    before = _snapshot()
    original = runner.AliasMapper.transform
    observed = []

    def inspect(mapper, batch):
        staged = StagingBatch.objects.get(
            ingestion_id=options[options.index("--ingestion-id") + 1],
            batch_number=batch.batch_number,
        )
        restored = runner.load_source_batch(
            ingestion_id=staged.ingestion_id, batch_number=batch.batch_number
        )
        assert batch.extracted_at == restored.extracted_at
        observed.append(batch.batch_number)
        return original(mapper, batch)

    monkeypatch.setattr(runner.AliasMapper, "transform", inspect)
    with CaptureQueriesContext(connections["default"]) as queries:
        assert runner.main(_set(options, "--mode", "resume")) == 0
    _no_writes(queries)
    assert observed == [0, 1] and _snapshot() == before
    assert _report(options)["dispositions"] == {
        "batch_verified_reused": 2,
        "attempt_verified_reused": 2,
    }


def test_pilot_is_explicitly_incomplete_then_resume_full(runner, options):
    assert runner.main([*options, "--max-batches", "1", "--trace-memory"]) == 0
    report = _report(options)
    assert report["status"] == "PILOT_PASSED" and report["complete_dataset_verified"] is False
    assert report["counts"]["batches"] == 1 and 0 < report["counts"]["source_rows"] < 18
    assert report["python_traced_peak_bytes"] > 0
    assert report["process_peak_rss_bytes"] is None
    assert runner.main(_set(options, "--mode", "resume")) == 0
    assert _report(options)["complete_dataset_verified"] is True


def test_mixed_interpretation_retains_unresolved_rows(runner, options, monkeypatch):
    original = runner.ExcelSource.extract

    def synthetic_dates(source):
        # Synthetic edge cases, not claims about the UCI fixture or real workbook.
        for batch in original(source):
            if batch.batch_number == 0:
                batch.data["InvoiceDate"] = batch.data["InvoiceDate"].astype(object)
                batch.data.iloc[0, batch.data.columns.get_loc("InvoiceDate")] = None
                batch.data.iloc[1, batch.data.columns.get_loc("InvoiceDate")] = "2026-10-20"
            yield batch

    monkeypatch.setattr(runner.ExcelSource, "extract", synthetic_dates)
    assert runner.main(options) == 0
    report = _report(options)
    assert report["counts"]["safe_rows"] == 16
    assert report["counts"]["unresolved_rows"] == 2
    assert report["counts"]["staged_rows"] == report["counts"]["outcome_rows"] == 18
    assert report["batches"][0]["issues"] == {"MISSING_DATE": 1, "DATE_ONLY": 1}
    assert (
        sum(item["timezone_statuses"].get("TIMEZONE_UNKNOWN", 0) for item in report["batches"])
        == 16
    )
    before = _snapshot()
    assert runner.main(_set(options, "--mode", "resume")) == 0
    assert _snapshot() == before


@pytest.mark.parametrize("profile_mode", ["off", "calls"])
def test_interruption_preserves_committed_batches_and_resumes(
    runner, options, monkeypatch, profile_mode
):
    options = _set(options, "--profile-mode", profile_mode)
    original = runner._process

    def interrupt(args, batch, item):
        if batch.batch_number == 1:
            raise KeyboardInterrupt
        return original(args, batch, item)

    with monkeypatch.context() as patch:
        patch.setattr(runner, "_process", interrupt)
        assert runner.main(options) == 1
    report = _report(options)
    assert report["failure_code"] == "INTERRUPTED" and not report["complete_dataset_verified"]
    assert report["counts"]["batches"] == 1
    ingestion = StagingIngestion.objects.get(pk=options[options.index("--ingestion-id") + 1])
    assert ingestion.status == "OPEN" and ingestion.batches.count() == 1
    assert runner.main(_set(options, "--mode", "resume")) == 0
    assert ingestion.batches.count() == 2


@pytest.mark.parametrize(
    "state", ["REGISTERED", "ACTIVE", "EXPIRED", "LEGACY", "FAILED", "CONFIG_CONFLICT"]
)
def test_resume_state_machine(runner, options, monkeypatch, state):
    original = runner.register_attempt

    def prepare(**kwargs):
        if state == "CONFIG_CONFLICT":
            kwargs["profile"] = SourceDateProfile(name="different-profile")
        attempt = original(**kwargs)
        if state in ("ACTIVE", "EXPIRED"):
            claim_attempt(attempt_id=attempt.pk, ttl_seconds=3600)
            if state == "EXPIRED":
                with connections["default"].cursor() as cursor:
                    cursor.execute(
                        "UPDATE etl_transformationattempt "
                        "SET claim_expires_at = clock_timestamp() - interval '1 second' "
                        "WHERE id=%s",
                        [attempt.pk],
                    )
        elif state == "LEGACY":
            start_attempt(attempt_id=attempt.pk)
        elif state == "FAILED":
            fail_attempt(attempt_id=attempt.pk, failure_code="SYNTHETIC", failure_stage="fixture")
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(runner, "register_attempt", prepare)
        assert runner.main(options) == 1
    before = _snapshot()
    ok = state in ("REGISTERED", "EXPIRED")
    with CaptureQueriesContext(connections["default"]) as queries:
        assert runner.main(_set(options, "--mode", "resume")) == (0 if ok else 1)
    if not ok:
        _no_writes(queries)
        assert _snapshot() == before
        assert not _report(options)["complete_dataset_verified"]
    else:
        attempts = TransformationAttempt.objects.filter(
            batch__ingestion_id=options[options.index("--ingestion-id") + 1]
        ).order_by("batch__batch_number")
        assert all(a.status == "SUCCEEDED" for a in attempts)
        assert attempts[0].claim_generation == (2 if state == "EXPIRED" else 1)


@pytest.mark.parametrize(
    "key,value",
    [
        ("--expected-db", "dataxai"),
        ("--expected-db", "dataxai_source"),
        ("--expected-db", "wrong-benchmark"),
        ("--expected-host", "wrong-host"),
        ("--expected-port", "5439"),
        ("--claim-ttl", "0"),
        ("--expected-sha256", "0" * 64),
    ],
)
def test_wrong_target_or_invalid_input_prevents_mutation(runner, options, key, value):
    before = _snapshot()
    with CaptureQueriesContext(connections["default"]) as queries:
        assert runner.main(_set(options, key, value)) != 0
    _no_writes(queries)
    assert _snapshot() == before


def test_authorization_and_non_postgres_rejected(runner, options, monkeypatch):
    args = list(options)
    args.remove("--authorize-benchmark-writes")
    before = _snapshot()
    assert runner.main(args) == 2
    with monkeypatch.context() as patch:
        patch.setattr(connections["default"], "vendor", "sqlite")
        assert runner.main(options) == 1
    assert _snapshot() == before


def test_accounting_failure_and_no_sensitive_output(runner, options, capsys):
    assert runner.main(_set(options, "--expected-rows", "19")) == 1
    report = _report(options)
    assert report["failure_code"] == "FULL_ACCOUNTING_MISMATCH"
    assert report["status"] == "FAILED" and not report["complete_dataset_verified"]
    output = capsys.readouterr().out + json.dumps(report)
    assert "Invoice" not in output and "Description" not in output
    assert str(FIXTURE) not in output and "source_name" not in output


def test_unexpected_error_is_sanitized_and_report_replaced_atomically(
    runner, options, monkeypatch, capsys
):
    def broken(*args, **kwargs):
        raise RuntimeError("password=SYNTHETIC-SECRET raw-customer-value owner-token")

    monkeypatch.setattr(runner, "load_source_batch", broken)
    assert runner.main(options) == 1
    report = _report(options)
    assert report["failure_code"] == "EXECUTION_ERROR"
    assert report["counts"] == {}
    assert "Fully reconciled batches" in report["counts_scope"]
    assert (
        StagingBatch.objects.filter(
            ingestion_id=options[options.index("--ingestion-id") + 1]
        ).count()
        == 1
    )
    assert "SYNTHETIC-SECRET" not in capsys.readouterr().out
    assert "raw-customer" not in json.dumps(_report(options))
    assert not list(Path(options[-1]).parent.glob(".staging-report-*"))


def test_source_identity_and_content_conflicts(runner, options, monkeypatch):
    assert runner.main(options) == 0
    before = _snapshot()
    resume = _set(options, "--mode", "resume")
    assert runner.main(_set(resume, "--source-reference", "changed-label")) == 1
    assert _snapshot() == before
    original = runner.ExcelSource.extract

    def changed(source):
        for batch in original(source):
            batch.data.iloc[0, 0] = "synthetic-changed-invoice"
            yield batch

    monkeypatch.setattr(runner.ExcelSource, "extract", changed)
    assert runner.main(resume) == 1
    assert _report(options)["failure_code"] == "SOURCE_CONTENT_CONFLICT"
    assert _snapshot() == before


def test_no_overwrite_existing_ingestion_and_report(runner, options, tmp_path):
    assert runner.main(options) == 0
    before = _snapshot()
    assert runner.main(options) == 1
    assert _report(options)["failure_code"] == "INGESTION_ALREADY_EXISTS"
    assert _snapshot() == before
    unrelated = tmp_path / "unrelated.json"
    unrelated.write_text('{"keep":true}', encoding="utf-8")
    assert runner.main(_set(options, "--report", unrelated)) == 2
    assert unrelated.read_text() == '{"keep":true}'


def test_outcome_tamper_is_detected_and_never_repaired(runner, options):
    assert runner.main(options) == 0
    row = TransformationRecordResult.objects.filter(
        attempt__batch__ingestion_id=options[options.index("--ingestion-id") + 1]
    ).first()
    with connections["default"].cursor() as cursor:
        cursor.execute(
            "UPDATE etl_transformationrecordresult SET result_payload='null'::jsonb WHERE id=%s",
            [row.pk],
        )
    before = _snapshot()
    assert runner.main(_set(options, "--mode", "resume")) == 1
    assert _snapshot() == before


def test_completion_rechecks_storage_after_successful_runner_read(runner, options, monkeypatch):
    read = runner.load_source_batch
    complete = runner.complete_attempt
    reads = []
    rejected_reads = []
    before_completion = []

    def observe_read(**identity):
        batch = read(**identity)
        reads.append((identity["ingestion_id"], batch.batch_number))
        return batch

    def corrupt_then_complete(**kwargs):
        attempt = TransformationAttempt.objects.select_related("batch").get(pk=kwargs["attempt_id"])
        assert reads == [(attempt.batch.ingestion_id, attempt.batch.batch_number)]
        assert attempt.status == "RUNNING" and not attempt.results.exists()
        _corrupt_with_matching_digest(attempt.batch, "scalar")
        before_completion.append(_snapshot())
        return complete(**kwargs)

    def observe_ledger_read(**identity):
        try:
            return read(**identity)
        except StagingIntegrityError:
            rejected_reads.append((identity["ingestion_id"], identity["batch_number"]))
            raise

    monkeypatch.setattr(runner, "load_source_batch", observe_read)
    monkeypatch.setattr(runner, "complete_attempt", corrupt_then_complete)
    monkeypatch.setattr(transformation_attempts, "load_source_batch", observe_ledger_read)
    assert runner.main(options) == 1
    assert _report(options)["failure_code"] == "ATTEMPT_INTEGRITY_ERROR"
    assert len(reads) == 1 and rejected_reads == reads
    assert before_completion == [_snapshot()]
    attempt = TransformationAttempt.objects.get(batch__ingestion_id=reads[0][0])
    assert attempt.status == "RUNNING" and attempt.finished_at is None
    assert attempt.safe_row_count is None and attempt.unresolved_row_count is None
    assert not attempt.results.exists()


def test_profiling_off_preserves_existing_report_and_does_not_install_hooks(
    runner, options, monkeypatch
):
    def forbidden(*args):
        pytest.fail("Disabled profiling must not construct instrumentation")

    monkeypatch.setattr(runner, "_Profiler", forbidden)
    assert runner._arguments(options).profile_mode == "off"
    assert runner.main(options) == 0
    before = _snapshot()
    assert runner.main(_set(_set(options, "--mode", "resume"), "--profile-mode", "off")) == 0
    report = _report(options)
    assert "profiling" not in report
    assert all("profiling" not in batch for batch in report["batches"])
    assert report["process_peak_rss_bytes"] is None
    assert report["process_peak_rss_note"] == "not measured"
    assert _snapshot() == before


def _sql_calls(profile, kinds):
    return sum(
        metric["execute_calls"]
        for stage in profile["sql"].values()
        for kind, metric in stage.items()
        if kind in kinds
    )


@pytest.mark.parametrize("mode", ["metrics", "calls"])
def test_profiled_fixture_and_read_only_replay_preserve_data_and_restore_settings(
    runner, options, monkeypatch, mode
):
    options = _set(options, "--profile-mode", mode)
    connection = connections["default"]
    monkeypatch.setattr(settings, "DEBUG", True)
    monkeypatch.setattr(connection, "force_debug_cursor", True)
    old_queries = list(connection.queries_log)
    old_wrappers = list(connection.execute_wrappers)
    old_profile = sys.getprofile()
    assert runner.main(options) == 0
    assert settings.DEBUG is True and connection.force_debug_cursor is True
    assert list(connection.queries_log) == old_queries
    assert connection.execute_wrappers == old_wrappers and sys.getprofile() is old_profile
    assert runner._active_profile.get() is None
    report = _report(options)
    assert report["counts"]["outcome_rows"] == report["counts"]["source_rows"] == 18
    assert sum(b["timezone_statuses"]["TIMEZONE_UNKNOWN"] for b in report["batches"]) == 18
    profile = report["profiling"]
    assert profile["schema"] == runner.PROFILE_SCHEMA and profile["mode"] == mode
    assert profile["elapsed"]["wall_seconds"] > 0
    assert profile["elapsed"]["cpu_seconds"] >= 0
    assert "Never sum" in profile["notes"]["overlap"]
    assert _sql_calls(profile["totals"], {"INSERT", "UPDATE"}) > 0
    for batch in report["batches"]:
        metrics = batch["profiling"]
        assert set(metrics["stages"]) == runner._STAGES
        assert all(m["calls"] == 1 for m in metrics["stages"].values())
        assert all(
            m["wall_seconds"] >= 0 and m["cpu_seconds"] >= 0 for m in metrics["stages"].values()
        )
        if mode == "calls":
            functions = metrics["functions"]
            # Runner and ledger each reconstruct independently before hashing stored content.
            assert functions["ser_encode_core"]["calls"] == 8
            assert functions["ser_decode_validate"]["calls"] == 5
            assert functions["ser_fingerprint"]["calls"] == 2
            assert functions["ser_encode_and_fingerprint"]["calls"] == 3
            assert functions["ser_decode_and_fingerprint"]["calls"] == 2
            assert functions["ser_validated_payload_fingerprint"]["calls"] == 5
            assert functions["read_reconstruct_verify"]["calls"] == 2
            assert functions["fingerprint_sha256"]["calls"] == 10
            assert functions["fingerprint_utf8"]["calls"] == 5
            assert functions["ledger_build_validate"]["calls"] == 1
            assert functions["staging_bulk_insert"]["calls"] == 1
            assert functions["outcome_bulk_insert"]["calls"] == 1
            assert functions["postgres_fetch"]["calls"] > 0
            assert functions["storage_json_decode_validate"]["calls"] > 0
            for metric in functions.values():
                for clock in ("wall", "cpu"):
                    assert (
                        0
                        <= metric[f"exclusive_observed_{clock}_seconds"]
                        <= metric[f"inclusive_{clock}_seconds"]
                    )
        else:
            assert metrics["functions"] == {}
    before = _snapshot()
    assert runner.main(_set(options, "--mode", "resume")) == 0
    resumed = _report(options)
    assert resumed["counts"] == report["counts"]
    assert resumed["dispositions"] == {"batch_verified_reused": 2, "attempt_verified_reused": 2}
    assert _sql_calls(resumed["profiling"]["totals"], {"INSERT", "UPDATE", "DELETE"}) == 0
    if mode == "calls":
        for batch in resumed["batches"]:
            functions = batch["profiling"]["functions"]
            assert functions["ser_encode_core"]["calls"] == 9
            assert functions["ser_decode_validate"]["calls"] == 6
            assert functions["ser_fingerprint"]["calls"] == 2
            assert functions["ser_encode_and_fingerprint"]["calls"] == 3
            assert functions["ser_decode_and_fingerprint"]["calls"] == 3
            assert functions["ser_validated_payload_fingerprint"]["calls"] == 6
            assert functions["fingerprint_utf8"]["calls"] == 6
            assert functions["fingerprint_sha256"]["calls"] == 12
            assert functions["read_reconstruct_verify"]["calls"] == 2
            assert functions["ledger_verify"]["calls"] == 1
    assert _snapshot() == before
    assert sys.getprofile() is old_profile and runner._active_profile.get() is None


def test_allowlisted_nested_times_and_constant_aggregate_size(runner, monkeypatch):
    clock = [0.0]

    def child():
        clock[0] += 5

    def parent():
        clock[0] += 2
        child()
        clock[0] += 3

    profiler = runner._Profiler("calls")
    profiler.codes = {parent.__code__: "parent", child.__code__: "child"}
    with monkeypatch.context() as patch:
        patch.setattr(runner.time, "perf_counter", lambda: clock[0])
        patch.setattr(runner.time, "process_time", lambda: clock[0] / 2)
        with profiler.activate():
            for _ in range(200):
                parent()
    functions = profiler.total["functions"]
    assert set(functions) == {"parent", "child"} and profiler.stack == []
    assert functions["parent"] == {
        "calls": 200,
        "inclusive_wall_seconds": 2000,
        "inclusive_cpu_seconds": 1000,
        "exclusive_observed_wall_seconds": 1000,
        "exclusive_observed_cpu_seconds": 500,
    }
    assert functions["child"]["inclusive_wall_seconds"] == 1000
    assert profiler.elapsed == {"wall_seconds": 2000, "cpu_seconds": 1000}


def test_sql_aggregation_excludes_parameters_and_debug_query_retention(runner, monkeypatch):
    connection = connections["default"]
    monkeypatch.setattr(settings, "DEBUG", True)
    monkeypatch.setattr(connection, "force_debug_cursor", True)
    before = list(connection.queries_log)
    profiler = runner._Profiler("metrics")
    with profiler.activate():
        with connection.cursor() as cursor:
            for _ in range(20):
                cursor.execute("SELECT %s /* SYNTHETIC-SQL-SECRET */", ["RAW-CUSTOMER-TOKEN"])
                assert cursor.fetchone() == ("RAW-CUSTOMER-TOKEN",)
            cursor.executemany("SELECT %s", [(1,), (2,)])
            with pytest.raises(DatabaseError):
                cursor.execute("SELECT 1 / 0")
    metric = profiler.total["sql"]["outside_stages"]["SELECT"]
    assert metric["execute_calls"] == 22 and metric["executemany_calls"] == 1
    assert metric["failed_calls"] == 1 and metric["wall_seconds"] > 0
    assert len(profiler.total["sql"]) == 1
    assert list(connection.queries_log) == before
    encoded = json.dumps(profiler.snapshot())
    assert "RAW-CUSTOMER-TOKEN" not in encoded and "SYNTHETIC-SQL-SECRET" not in encoded
    assert "SELECT %s" not in encoded


@pytest.mark.parametrize("available", [True, False])
def test_windows_memory_measurement_and_controlled_unavailability(runner, monkeypatch, available):
    def memory():
        if not available:
            raise OSError("SYNTHETIC-SECRET")
        return 123456

    monkeypatch.setattr(runner.sys, "platform", "win32")
    monkeypatch.setattr(runner, "_windows_peak_working_set", memory)
    result = runner._process_memory()
    assert result["peak_working_set_bytes"] == (123456 if available else None)
    assert result["status"] == ("MEASURED" if available else "UNAVAILABLE")
    assert "process lifetime" in result["scope"]
    assert "SYNTHETIC-SECRET" not in json.dumps(result)


def test_memory_is_not_fabricated_on_unsupported_platform(runner, monkeypatch):
    monkeypatch.setattr(runner.sys, "platform", "unsupported")
    result = runner._process_memory()
    assert result["peak_working_set_bytes"] is None and result["status"] == "UNSUPPORTED_PLATFORM"


@pytest.mark.parametrize("mode", ["metrics", "calls"])
def test_failure_restores_instrumentation_and_sanitizes_profile(
    runner, options, monkeypatch, capsys, mode
):
    connection = connections["default"]
    old_profile = sys.getprofile()
    old_wrappers = list(connection.execute_wrappers)
    monkeypatch.setattr(settings, "DEBUG", True)
    monkeypatch.setattr(connection, "force_debug_cursor", True)
    before_queries = list(connection.queries_log)

    def broken(*args):
        # Fail inside observed functions, exercising exception unwinding too.
        from etl.serialization import encode_source_batch

        encode_source_batch("SYNTHETIC-SECRET-CUSTOMER-TOKEN")

    monkeypatch.setattr(runner, "_source_check", broken)
    assert runner.main(_set(options, "--profile-mode", mode)) == 1
    report = _report(options)
    assert report["status"] == "FAILED" and report["counts"] == {}
    assert not report["complete_dataset_verified"]
    assert report["profiling"]["elapsed"]["wall_seconds"] > 0
    output = json.dumps(report) + capsys.readouterr().out
    assert "SYNTHETIC-SECRET" not in output and "owner-token" not in output
    assert connection.execute_wrappers == old_wrappers and sys.getprofile() is old_profile
    assert settings.DEBUG is True and connection.force_debug_cursor is True
    assert list(connection.queries_log) == before_queries
    assert runner._active_profile.get() is None


def test_existing_profiler_is_not_overwritten_and_conflict_prevents_writes(runner, options):
    before = _snapshot()
    prior = sys.getprofile()

    def existing(frame, event, arg):
        pass

    sys.setprofile(existing)
    try:
        assert runner.main(_set(options, "--profile-mode", "calls")) == 1
        assert sys.getprofile() is existing
    finally:
        sys.setprofile(prior)
    assert _report(options)["failure_code"] == "PROFILE_ALREADY_ACTIVE"
    assert _snapshot() == before


def test_invalid_profile_mode_rejected_before_writes(runner, options):
    before = _snapshot()
    assert runner.main(_set(options, "--profile-mode", "SYNTHETIC-SECRET")) == 2
    assert _snapshot() == before
