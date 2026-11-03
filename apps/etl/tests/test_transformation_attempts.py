"""Real PostgreSQL attempt/outcome ledgers; all source records are synthetic."""

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from queue import Queue
from threading import Barrier
from uuid import uuid4

import pandas as pd
import pytest
from django.db import IntegrityError, OperationalError, connections, transaction
from django.db.models.query import QuerySet
from django.test.utils import CaptureQueriesContext

from etl import transformation_attempts as service
from etl.exceptions import (
    AttemptConflictError,
    AttemptIntegrityError,
    AttemptRegistrationError,
    TypeConversionError,
)
from etl.models import (
    StagingIngestion,
    StagingRecord,
    TransformationAttempt,
    TransformationRecordResult,
)
from etl.serialization import encode_source_batch
from etl.sources.contract import CANONICAL_COLUMNS, SourceBatch
from etl.staging import stage_source_batch
from etl.staging_reader import load_source_batch
from etl.tests.test_staging_constraints import staging_postgres as staging_postgres
from etl.tests.test_staging_persistence import application_postgres as application_postgres
from etl.transforms.alias_mapper import AliasMapper
from etl.transforms.date_contract import DateIssueCode, SourceDateProfile, TimezoneStatus
from etl.transforms.date_normalizer import DateNormalizer

VERSION = "date-normalizer-v1"


@pytest.fixture
def make_case():
    def create(dates=("2026-10-20 14:30:00", None, "2026-10-20"), profile=None, index=None):
        profile = SourceDateProfile() if profile is None else profile
        ingestion = StagingIngestion.objects.create(
            source_kind="CSV", source_reference="synthetic-attempt-test"
        )
        rows = [
            [
                "C001",
                "M",
                "synthetic \u0627\u0631\u062f\u0648\x00",
                -2,
                date,
                Decimal("-0.0100"),
                None,
                "Pakistan",
            ]
            for date in dates
        ]
        frame = pd.DataFrame(rows, columns=CANONICAL_COLUMNS, dtype=object)
        frame.index = (
            pd.Index(["duplicate\x00index"] * len(rows), name="source-position")
            if index is None
            else index.copy()
        )
        source = SourceBatch(
            frame, "source\x00name", 0, datetime(2026, 10, 20, tzinfo=timezone.utc)
        )
        staged = stage_source_batch(ingestion_id=ingestion.pk, batch=source)
        result = DateNormalizer(profile).apply(AliasMapper().transform(source))
        return ingestion, source, staged, result

    return create


def _register(case, **overrides):
    ingestion, _, _, result = case
    kwargs = dict(
        ingestion_id=ingestion.pk,
        batch_number=0,
        attempt_key=uuid4(),
        profile=result.profile,
        transformation_version=VERSION,
    )
    kwargs.update(overrides)
    return service.register_attempt(**kwargs)


def _running(case):
    return service.start_attempt(attempt_id=_register(case).pk)


def _complete(attempt, result):
    return service.complete_attempt(
        attempt_id=attempt.pk, result=result, transformation_version=VERSION
    )


def _failure(attempt, **overrides):
    kwargs = dict(
        attempt_id=attempt.pk,
        failure_code="TYPE_CONVERSION",
        failure_stage="type_conversion",
        diagnostics={"row_position": 0, "retryable": False},
    )
    kwargs.update(overrides)
    return service.fail_attempt(**kwargs)


def _snapshot(attempt):
    return (
        TransformationAttempt.objects.filter(pk=attempt.pk).values().get(),
        list(TransformationRecordResult.objects.filter(attempt=attempt).order_by("pk").values()),
    )


def _raw_rows(staged):
    return list(staged.records.order_by("row_position").values())


def _assert_no_writes(queries):
    assert not any(
        query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        for query in queries
    )


def _changed(result, **fields):
    """Tamper after construction to test the mutable/caller-supplied boundary."""
    altered = deepcopy(result)
    for key, value in fields.items():
        object.__setattr__(altered, key, value)
    return altered


def test_registration_config_hash_retry_and_distinct_key(make_case):
    case = make_case(
        profile=SourceDateProfile(
            name="retail-source",
            date_order="DMY",
            timezone="Asia/Karachi",
            provenance="Synthetic export specification",
        )
    )
    key = uuid4()
    attempt = _register(case, attempt_key=key)
    assert attempt.status == "REGISTERED"
    assert attempt.started_at is attempt.finished_at is None
    assert attempt.safe_row_count is attempt.unresolved_row_count is None
    expected = {
        "schema": "interpretation-config-v1",
        "date_profile": {
            "name": "retail-source",
            "date_order": "DMY",
            "timezone": "Asia/Karachi",
            "provenance": "Synthetic export specification",
        },
    }
    assert attempt.configuration == expected
    canonical = json.dumps(
        expected, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    assert attempt.configuration_hash == hashlib.sha256(canonical).hexdigest()
    before = _snapshot(attempt)
    with CaptureQueriesContext(connections["default"]) as queries:
        retry = _register(case, attempt_key=key)
    _assert_no_writes(queries)
    assert retry.pk == attempt.pk and _snapshot(attempt) == before
    other = _register(case)
    assert other.pk != attempt.pk and other.configuration_hash == attempt.configuration_hash
    assert (attempt.claim_generation, attempt.claim_owner, attempt.claim_expires_at) == (
        0,
        None,
        None,
    )


@pytest.mark.parametrize("different", ["version", "profile"])
def test_registration_conflict_never_overwrites(make_case, different):
    case = make_case()
    attempt = _register(case)
    before = _snapshot(attempt)
    changes = (
        {"transformation_version": "v2"}
        if different == "version"
        else {"profile": SourceDateProfile(name="other-source")}
    )
    with pytest.raises(AttemptConflictError):
        _register(case, attempt_key=attempt.attempt_key, **changes)
    assert _snapshot(attempt) == before


def test_invalid_identities_versions_backend_and_missing_rows(make_case, monkeypatch):
    case = make_case()
    for field, value in (
        ("ingestion_id", str(case[0].pk)),
        ("ingestion_id", True),
        ("attempt_key", str(uuid4())),
        ("attempt_key", None),
        ("batch_number", True),
        ("batch_number", -1),
        ("batch_number", 0.0),
        ("transformation_version", ""),
        ("transformation_version", "   "),
        ("transformation_version", "v" * 129),
    ):
        with pytest.raises(AttemptRegistrationError):
            _register(case, **{field: value})
    for changes in ({"ingestion_id": uuid4()}, {"batch_number": 1}):
        with pytest.raises(AttemptRegistrationError):
            _register(case, **changes)
    for identity in (True, 0, -1, "1", None, 2**63 - 1):
        with pytest.raises(AttemptRegistrationError):
            service.start_attempt(attempt_id=identity)
    monkeypatch.setattr(connections["default"], "vendor", "sqlite")
    with pytest.raises(AttemptRegistrationError):
        _register(case)
    assert not TransformationAttempt.objects.filter(batch=case[2]).exists()


@pytest.mark.parametrize(
    "value",
    [
        "nul\x00",
        "bad\ud800",
        1,
        {},
        float("nan"),
        float("inf"),
        Decimal("1"),
        "password=synthetic-secret",
    ],
)
def test_profile_jsonb_unsafe_values_rejected_before_insert(make_case, value):
    case = make_case()
    profile = SourceDateProfile()
    object.__setattr__(profile, "provenance", value)
    with pytest.raises(AttemptRegistrationError) as caught:
        _register(case, profile=profile)
    assert "synthetic-secret" not in str(caught.value)
    assert not TransformationAttempt.objects.filter(batch=case[2]).exists()


def test_profile_type_and_revalidation(make_case):
    case = make_case()
    for invalid in (None, {}, {"name": "unconfigured"}):
        with pytest.raises(AttemptRegistrationError):
            _register(case, profile=invalid)
    profile = SourceDateProfile()
    object.__setattr__(profile, "timezone", "Invalid/Timezone")
    with pytest.raises(AttemptRegistrationError):
        _register(case, profile=profile)


def test_configuration_character_boundary_matches_postgres(make_case):
    case = make_case()
    template = {
        "schema": "interpretation-config-v1",
        "date_profile": {
            "name": "unconfigured",
            "date_order": None,
            "timezone": None,
            "provenance": "",
        },
    }
    available = 16384 - len(json.dumps(template, ensure_ascii=False, allow_nan=False))
    profile = SourceDateProfile(provenance="a" * available)
    attempt = _register(case, profile=profile)
    with connections["default"].cursor() as cursor:
        cursor.execute(
            "SELECT length(configuration::text) FROM etl_transformationattempt WHERE id = %s",
            [attempt.pk],
        )
        assert cursor.fetchone()[0] == 16384
    with pytest.raises(AttemptRegistrationError):
        _register(case, profile=SourceDateProfile(provenance="a" * (available + 1)))


@pytest.mark.parametrize("damage", ["hash", "nonobject", "profile"])
def test_registration_rechecks_stored_configuration(make_case, damage):
    case = make_case()
    attempt = _register(case)
    changes = {"configuration_hash": "0" * 64}
    if damage == "nonobject":
        changes = {"configuration": []}
    elif damage == "profile":
        config = deepcopy(attempt.configuration)
        config["date_profile"]["date_order"] = "unknown"
        changes = {"configuration": config}
    TransformationAttempt.objects.filter(pk=attempt.pk).update(**changes)
    before = _snapshot(attempt)
    with pytest.raises(AttemptIntegrityError):
        _register(case, attempt_key=attempt.attempt_key)
    assert _snapshot(attempt) == before


def test_start_is_idempotent_and_reserved_claims_remain_unchanged(make_case):
    case = make_case()
    attempt = _register(case)
    expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    TransformationAttempt.objects.filter(pk=attempt.pk).update(
        claim_generation=7, claim_owner="reserved-owner", claim_expires_at=expiry
    )
    running = service.start_attempt(attempt_id=attempt.pk)
    assert running.status == "RUNNING" and running.started_at.tzinfo is not None
    before = _snapshot(running)
    with CaptureQueriesContext(connections["default"]) as queries:
        again = service.start_attempt(attempt_id=attempt.pk)
    _assert_no_writes(queries)
    assert again.started_at == running.started_at and _snapshot(again) == before
    succeeded = _complete(running, case[3])
    assert (succeeded.claim_generation, succeeded.claim_owner, succeeded.claim_expires_at) == (
        7,
        "reserved-owner",
        expiry,
    )
    with pytest.raises(AttemptConflictError):
        service.start_attempt(attempt_id=attempt.pk)


@pytest.mark.parametrize("operation", ["start", "complete", "fail"])
def test_transitions_revalidate_stored_configuration(make_case, operation):
    case = make_case()
    attempt = _running(case) if operation == "complete" else _register(case)
    TransformationAttempt.objects.filter(pk=attempt.pk).update(configuration_hash="0" * 64)
    before = _snapshot(attempt)
    with pytest.raises(AttemptIntegrityError):
        if operation == "start":
            service.start_attempt(attempt_id=attempt.pk)
        elif operation == "complete":
            _complete(attempt, case[3])
        else:
            _failure(attempt)
    assert _snapshot(attempt) == before


@pytest.mark.parametrize(
    "dates, expected",
    [
        (("2026-10-20 14:30:00", "2026-10-20 14:30:00"), (2, 0)),
        ((None, "2026-10-20"), (0, 2)),
        (("2026-10-20T14:30:00Z", None, "2026-10-20"), (1, 2)),
        ((), (0, 0)),
    ],
)
def test_exact_outcome_ledger_and_raw_preservation(make_case, dates, expected):
    case = make_case(dates)
    _, _, staged, result = case
    before = _raw_rows(staged)
    attempt = _running(case)
    finished = _complete(attempt, result)
    assert finished.status == "SUCCEEDED" and finished.finished_at.tzinfo is not None
    assert (finished.safe_row_count, finished.unresolved_row_count) == expected
    rows = list(finished.results.order_by("staged_record__row_position"))
    staged_ids = list(staged.records.order_by("row_position").values_list("pk", flat=True))
    assert [row.staged_record_id for row in rows] == staged_ids
    assert len(rows) == sum(expected) == len(dates)
    issues = {issue.row.position: issue.code.value for issue in result.issues}
    for position, row in enumerate(rows):
        evidence = result.date_evidence[position]
        assert row.batch_id == staged.pk and row.attempt_id == attempt.pk
        assert row.result_payload is None
        assert row.outcome == ("SAFE" if position in result.safe_positions else "UNRESOLVED")
        assert row.interpretation_evidence == {
            "schema": "date-outcome-v1",
            "timezone_status": evidence.timezone_status.value,
            "timezone_origin": evidence.timezone_origin,
            "issue_code": issues.get(position),
        }
    assert _raw_rows(staged) == before
    before_attempt = _snapshot(finished)
    with CaptureQueriesContext(connections["default"]) as queries:
        replay = _complete(finished, deepcopy(result))
    _assert_no_writes(queries)
    assert replay.pk == finished.pk and _snapshot(replay) == before_attempt
    assert _register(case, attempt_key=attempt.attempt_key).pk == finished.pk


def test_safe_unknown_timezone_is_not_validation_or_repair(make_case):
    case = make_case(("2026-10-20 14:30:00",))
    completed = _complete(_running(case), case[3])
    row = completed.results.get()
    assert row.outcome == "SAFE"
    assert row.interpretation_evidence == {
        "schema": "date-outcome-v1",
        "timezone_status": "TIMEZONE_UNKNOWN",
        "timezone_origin": "unspecified",
        "issue_code": None,
    }
    assert row.result_payload is None
    assert case[1].data.iloc[0]["Quantity"] == -2
    assert case[1].data.iloc[0]["Price"].as_tuple() == Decimal("-0.0100").as_tuple()


def test_successful_result_inserts_are_bounded_and_not_per_row(make_case, monkeypatch):
    case = make_case()
    attempt = _running(case)
    original = QuerySet.bulk_create
    sizes = []
    assert service._INSERT_BATCH_SIZE == 1000

    def observed(query, objects, **kwargs):
        assert query.model is TransformationRecordResult and query.db == "default"
        assert kwargs["batch_size"] == 2
        sizes.append(len(objects))
        return original(query, objects, **kwargs)

    monkeypatch.setattr(service, "_INSERT_BATCH_SIZE", 2)
    monkeypatch.setattr(QuerySet, "bulk_create", observed)
    completed = _complete(attempt, case[3])
    assert sizes == [2, 1] and completed.results.count() == 3


@pytest.mark.parametrize(
    "damage",
    [
        "source",
        "batch",
        "extraction",
        "profile",
        "count",
        "duplicate",
        "missing",
        "extra",
        "misordered",
        "bool_position",
        "evidence_source",
        "evidence_position",
        "issue_position",
        "issue_code",
        "timezone_status",
        "timezone_origin",
        "safe_issue",
        "frame_count",
        "schema",
    ],
)
def test_invalid_result_provenance_or_accounting_rolls_back(make_case, damage):
    case = make_case()
    result = deepcopy(case[3])
    if damage in {"source", "batch", "extraction"}:
        values = {
            "source": {"source_name": "foreign"},
            "batch": {"batch_number": 1},
            "extraction": {"extracted_at": result.safe_batch.extracted_at + timedelta(days=1)},
        }
        result = _changed(result, safe_batch=replace(result.safe_batch, **values[damage]))
    elif damage == "profile":
        result = _changed(result, profile=SourceDateProfile(name="other"))
    elif damage == "count":
        result = _changed(result, input_rows=4)
    elif damage in {"duplicate", "missing", "extra", "misordered", "bool_position"}:
        positions = {
            "duplicate": (1, 1),
            "missing": (1,),
            "extra": (1, 2, 3),
            "misordered": (2, 1),
            "bool_position": (True, 2),
        }[damage]
        result = _changed(result, unresolved_positions=positions)
    elif damage in {"evidence_source", "evidence_position", "timezone_status", "timezone_origin"}:
        evidence = list(result.date_evidence)
        first = evidence[0]
        if damage == "evidence_source":
            first = replace(first, row=replace(first.row, source_name="foreign"))
        elif damage == "evidence_position":
            first = replace(first, row=replace(first.row, position=2))
        elif damage == "timezone_status":
            first = replace(first, timezone_status="not-a-status")
        else:
            first = replace(first, timezone_origin="credential=synthetic-private")
        evidence[0] = first
        result = _changed(result, date_evidence=tuple(evidence))
    elif damage in {"issue_position", "issue_code", "safe_issue"}:
        issues = list(result.issues)
        if damage == "issue_position":
            issues[0] = replace(issues[0], row=replace(issues[0].row, position=0))
        elif damage == "issue_code":
            issues[0] = replace(issues[0], code="not-an-issue")
        else:
            evidence = list(result.date_evidence)
            evidence[0] = replace(evidence[0], timezone_status=TimezoneStatus.UNRESOLVED)
            result = _changed(result, date_evidence=tuple(evidence))
        result = _changed(result, issues=tuple(issues))
    elif damage == "frame_count":
        result.safe_batch.data.drop(result.safe_batch.data.index, inplace=True)
    else:
        result.safe_batch.data.drop(columns="quantity", inplace=True)
    attempt = _running(case)
    before = _snapshot(attempt)
    with pytest.raises(AttemptIntegrityError) as caught:
        _complete(attempt, result)
    assert "synthetic-private" not in str(caught.value)
    assert _snapshot(attempt) == before


def test_completion_state_and_version_conflicts(make_case):
    case = make_case()
    attempt = _register(case)
    with pytest.raises(AttemptConflictError):
        _complete(attempt, case[3])
    service.start_attempt(attempt_id=attempt.pk)
    with pytest.raises(AttemptConflictError):
        service.complete_attempt(attempt_id=attempt.pk, result=case[3], transformation_version="v2")
    _failure(attempt)
    with pytest.raises(AttemptConflictError):
        _complete(attempt, case[3])
    with pytest.raises(AttemptConflictError):
        service.start_attempt(attempt_id=attempt.pk)


def _different_ledger(result):
    issues = list(result.issues)
    issues[0] = replace(issues[0], code=DateIssueCode.INVALID_CALENDAR_DATE)
    return replace(result, issues=tuple(issues))


def test_conflicting_success_retry_preserves_committed_ledger(make_case):
    case = make_case()
    finished = _complete(_running(case), case[3])
    before = _snapshot(finished)
    with pytest.raises(AttemptConflictError):
        _complete(finished, _different_ledger(case[3]))
    assert _snapshot(finished) == before


@pytest.mark.parametrize("damage", ["missing", "count", "payload", "evidence", "outcome"])
def test_corrupt_committed_ledger_rejected_without_repair(make_case, damage):
    case = make_case()
    attempt = _complete(_running(case), case[3])
    first = attempt.results.order_by("pk").first()
    if damage == "missing":
        first.delete()
    elif damage == "count":
        TransformationAttempt.objects.filter(pk=attempt.pk).update(safe_row_count=9)
    elif damage == "payload":
        TransformationRecordResult.objects.filter(pk=first.pk).update(result_payload={})
    elif damage == "evidence":
        TransformationRecordResult.objects.filter(pk=first.pk).update(interpretation_evidence={})
    else:
        TransformationRecordResult.objects.filter(pk=first.pk).update(outcome="UNRESOLVED")
    before = _snapshot(attempt)
    with pytest.raises((AttemptIntegrityError, AttemptConflictError)):
        _complete(attempt, case[3])
    assert _snapshot(attempt) == before


def test_staged_row_gap_and_foreign_result_identity_are_rejected(make_case):
    case = make_case()
    attempt = _running(case)
    original = case[2].records.order_by("row_position").first()
    StagingRecord.objects.filter(pk=original.pk).update(row_position=9)
    with pytest.raises(AttemptIntegrityError):
        _complete(attempt, case[3])
    assert not attempt.results.exists()
    other_case = make_case()
    foreign = other_case[2].records.first()
    # Real PostgreSQL composite FK remains a second line of defense.
    with pytest.raises(IntegrityError), transaction.atomic():
        TransformationRecordResult.objects.create(
            batch=case[2], attempt=attempt, staged_record=foreign, outcome="SAFE"
        )


def test_bounded_bulk_insert_failure_rolls_back_and_can_be_failed_separately(
    make_case, monkeypatch
):
    case = make_case()
    attempt = _running(case)
    before = _snapshot(attempt)
    original = QuerySet.bulk_create
    calls = []

    def interrupted(query, objects, **kwargs):
        assert query.model is TransformationRecordResult and query.db == "default"
        assert kwargs["batch_size"] == 1 and len(objects) == 1
        calls.append(len(objects))
        if len(calls) == 2:
            assert attempt.results.count() == 1
            raise RuntimeError("synthetic insert interruption")
        return original(query, objects, **kwargs)

    monkeypatch.setattr(service, "_INSERT_BATCH_SIZE", 1)
    with monkeypatch.context() as patch:
        patch.setattr(QuerySet, "bulk_create", interrupted)
        with pytest.raises(RuntimeError, match="synthetic insert interruption"):
            _complete(attempt, case[3])
    assert calls == [1, 1] and _snapshot(attempt) == before
    failed = _failure(attempt)
    assert failed.status == "FAILED" and not failed.results.exists()


def test_successful_completion_is_rolled_back_by_outer_transaction(make_case):
    case = make_case()
    attempt = _running(case)
    before = _snapshot(attempt)
    with pytest.raises(RuntimeError, match="outer rollback"):
        with transaction.atomic():
            result = _complete(attempt, case[3])
            assert result.status == "SUCCEEDED" and result.results.count() == 3
            raise RuntimeError("outer rollback")
    assert _snapshot(attempt) == before


@pytest.mark.parametrize("started", [False, True])
def test_controlled_failure_is_atomic_idempotent_and_preserves_claims(make_case, started):
    case = make_case()
    attempt = _running(case) if started else _register(case)
    started_at = attempt.started_at
    TransformationAttempt.objects.filter(pk=attempt.pk).update(claim_generation=4)
    failed = _failure(attempt)
    assert failed.status == "FAILED" and failed.finished_at.tzinfo is not None
    assert failed.started_at == started_at and failed.claim_generation == 4
    assert failed.safe_row_count is failed.unresolved_row_count is None
    assert not failed.results.exists()
    before = _snapshot(failed)
    with CaptureQueriesContext(connections["default"]) as queries:
        retry = _failure(failed)
    _assert_no_writes(queries)
    assert retry.pk == failed.pk and _snapshot(retry) == before
    with pytest.raises(AttemptConflictError):
        _failure(failed, failure_code="OTHER_FAILURE")
    assert _snapshot(failed) == before


@pytest.mark.parametrize(
    "change",
    [
        {"failure_code": ""},
        {"failure_code": "X" * 65},
        {"failure_stage": "stage\x00"},
        {"failure_stage": "bad\ud800"},
        {"diagnostics": []},
        {"diagnostics": {"traceback": "synthetic-private"}},
        {"diagnostics": {"row_position": True}},
        {"diagnostics": {"row_position": -1}},
        {"diagnostics": {"row_position": float("nan")}},
        {"diagnostics": {"row_position": float("inf")}},
        {"diagnostics": {"row_position": Decimal("1")}},
        {"diagnostics": {"retryable": "false"}},
        {"diagnostics": {"row_position": 2**63}},
        {"diagnostics": {"message": "a" * 4097}},
    ],
)
def test_failure_metadata_is_strict_bounded_and_non_sensitive(make_case, change):
    attempt = _register(make_case())
    before = _snapshot(attempt)
    with pytest.raises(AttemptRegistrationError) as caught:
        _failure(attempt, **change)
    assert "synthetic-private" not in str(caught.value)
    assert _snapshot(attempt) == before


def test_no_invented_outcomes_after_type_conversion_exception(make_case):
    case = make_case()
    attempt = _running(case)
    raw = case[1].data.copy(deep=True)
    raw.iloc[0, raw.columns.get_loc("Quantity")] = "invalid synthetic quantity"
    with pytest.raises(TypeConversionError):
        DateNormalizer().apply(AliasMapper().transform(replace(case[1], data=raw)))
    failed = _failure(attempt, diagnostics=None)
    assert failed.status == "FAILED" and not failed.results.exists()
    assert failed.safe_row_count is failed.unresolved_row_count is None


def test_failure_cannot_overwrite_success_or_delete_unexpected_rows(make_case):
    case = make_case()
    succeeded = _complete(_running(case), case[3])
    before = _snapshot(succeeded)
    with pytest.raises(AttemptConflictError):
        _failure(succeeded)
    assert _snapshot(succeeded) == before
    unfinished = _register(case)
    TransformationRecordResult.objects.create(
        batch=case[2], attempt=unfinished, staged_record=case[2].records.first(), outcome="SAFE"
    )
    before = _snapshot(unfinished)
    with pytest.raises(AttemptIntegrityError):
        _failure(unfinished)
    assert _snapshot(unfinished) == before


@pytest.mark.parametrize("error", [OperationalError, RuntimeError])
def test_unexpected_lookup_errors_propagate(make_case, monkeypatch, error):
    attempt = _register(make_case())

    def broken(*args, **kwargs):
        raise error("synthetic lookup interruption")

    monkeypatch.setattr(QuerySet, "get", broken)
    with pytest.raises(error, match="synthetic lookup interruption"):
        service.start_attempt(attempt_id=attempt.pk)


def _concurrent_locked(model, pk, calls, setup=None):
    """Require two independent PostgreSQL sessions to wait on a real DB lock."""
    barrier, backend_ids = Barrier(3), Queue()

    def submit(call):
        connection = connections["default"]
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '15s'")
                cursor.execute("SELECT pg_backend_pid()")
                backend_ids.put(cursor.fetchone()[0])
            barrier.wait(timeout=10)
            try:
                return call().pk
            except AttemptConflictError:
                return "conflict"
        finally:
            connection.close()

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        with transaction.atomic():
            if setup is not None:
                setup()
            else:
                model.objects.select_for_update().get(pk=pk)
            futures = [executor.submit(submit, call) for call in calls]
            barrier.wait(timeout=10)
            pids = [backend_ids.get(timeout=5), backend_ids.get(timeout=5)]
            deadline, waiting = time.monotonic() + 10, 0
            while waiting < 2 and time.monotonic() < deadline:
                with connections["default"].cursor() as cursor:
                    cursor.execute(
                        "SELECT count(DISTINCT pid) FROM pg_locks "
                        "WHERE pid = ANY(%s) AND NOT granted",
                        [pids],
                    )
                    waiting = cursor.fetchone()[0]
                if waiting < 2:
                    time.sleep(0.01)
            assert waiting == 2, "Both PostgreSQL sessions must wait on the selected lock"
        return [future.result(timeout=20) for future in futures]
    finally:
        executor.shutdown(wait=True)


def test_concurrent_same_key_registration_creates_one_attempt(make_case):
    case, key = make_case(), uuid4()
    original = []
    results = _concurrent_locked(
        None,
        None,
        [lambda: _register(case, attempt_key=key), lambda: _register(case, attempt_key=key)],
        setup=lambda: original.append(_register(case, attempt_key=key)),
    )
    assert results[0] == results[1] == original[0].pk
    assert TransformationAttempt.objects.filter(batch=case[2]).count() == 1


@pytest.mark.parametrize("conflicting", [False, True])
def test_concurrent_completion_serializes_and_never_overwrites(make_case, conflicting):
    case = make_case()
    attempt = _running(case)
    second = _different_ledger(case[3]) if conflicting else deepcopy(case[3])
    results = _concurrent_locked(
        TransformationAttempt,
        attempt.pk,
        [lambda: _complete(attempt, case[3]), lambda: _complete(attempt, second)],
    )
    assert results.count("conflict") == int(conflicting)
    assert all(value in (attempt.pk, "conflict") for value in results)
    attempt.refresh_from_db()
    assert attempt.status == "SUCCEEDED" and attempt.results.count() == len(case[1])
    assert attempt.safe_row_count + attempt.unresolved_row_count == len(case[1])


@pytest.mark.parametrize(
    "original, changed",
    [(Decimal("1.0"), Decimal("1.00")), (0.0, -0.0)],
    ids=["decimal-scale", "signed-zero"],
)
@pytest.mark.parametrize("location", ["safe_index", "unresolved_index", "evidence", "issue"])
def test_changed_index_representation_is_rejected(make_case, original, changed, location):
    """Pandas numeric equality must not erase SER-v1's exact label representation."""
    index = pd.Index([original, original], dtype=object, name="representation")
    case = make_case(("2026-10-20 14:30:00", None), index=index)
    result = deepcopy(case[3])
    if location in {"safe_index", "unresolved_index"}:
        split = result.safe_batch if location == "safe_index" else result.unresolved_batch
        split.data.index = pd.Index([changed], dtype=object, name=index.name)
    elif location == "evidence":
        evidence = list(result.date_evidence)
        evidence[0] = replace(evidence[0], row=replace(evidence[0].row, index_label=changed))
        result = replace(result, date_evidence=tuple(evidence))
    else:
        issue = result.issues[0]
        result = replace(
            result, issues=(replace(issue, row=replace(issue.row, index_label=changed)),)
        )
    attempt = _running(case)
    before = _snapshot(attempt)
    original_rows = _raw_rows(case[2])
    with pytest.raises(AttemptIntegrityError):
        _complete(attempt, result)
    assert _snapshot(attempt) == before
    assert _raw_rows(case[2]) == original_rows


@pytest.mark.parametrize(
    "index",
    [
        pd.RangeIndex(4, 10, 2, name="range"),
        pd.Index(["a\x00", "b", "c"], dtype=object, name="object"),
        pd.Index([4, 5, 6], dtype="int64", name="integer"),
        pd.Index(pd.array([4, pd.NA, 6], dtype="Int64"), name="nullable"),
        pd.DatetimeIndex(
            ["2026-10-20T14:30:00.123456789Z", "2026-10-21T00:00:00Z", "2026-10-22T00:00:00Z"],
            name="datetime",
        ),
        pd.Index(["duplicate", "duplicate", "duplicate"], dtype=object, name="duplicates"),
        pd.Index(
            [Decimal("1.0"), Decimal("1.00"), Decimal("-0.000")], dtype=object, name="decimal"
        ),
        pd.Index([0.0, -0.0, 1.0], dtype="float64", name="floating"),
    ],
    ids=["range", "object", "integer", "nullable", "datetime", "duplicates", "decimal", "zero"],
)
def test_supported_index_representations_preserve_positional_lineage(make_case, index):
    case = make_case(index=index)
    expected_source = encode_source_batch(case[1])
    before_rows = _raw_rows(case[2])
    completed = _complete(_running(case), case[3])
    assert completed.status == "SUCCEEDED"
    assert (completed.safe_row_count, completed.unresolved_row_count) == (1, 2)
    assert list(
        completed.results.order_by("staged_record__row_position").values_list(
            "staged_record_id", flat=True
        )
    ) == list(case[2].records.order_by("row_position").values_list("pk", flat=True))
    assert _complete(completed, deepcopy(case[3])).pk == completed.pk
    restored = load_source_batch(ingestion_id=case[0].pk, batch_number=0)
    assert encode_source_batch(restored) == expected_source
    assert _raw_rows(case[2]) == before_rows


@pytest.mark.parametrize("conflicting", [False, True])
def test_concurrent_first_registration_same_key(make_case, monkeypatch, conflicting):
    """Both independent connections see absence before either performs its real insert."""
    case, key = make_case(), uuid4()
    assert not TransformationAttempt.objects.filter(batch=case[2], attempt_key=key).exists()
    barrier, observed = Barrier(2), Queue()
    original_first = QuerySet.first

    def synchronized_first(query):
        result = original_first(query)
        if query.model is TransformationAttempt:
            assert query.db == "default" and result is None
            with connections["default"].cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                observed.put(cursor.fetchone()[0])
            barrier.wait(timeout=10)
        return result

    profiles = [
        case[3].profile,
        SourceDateProfile(name="other-source") if conflicting else case[3].profile,
    ]

    def submit(profile):
        connection = connections["default"]
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '15s'")
            try:
                return _register(case, attempt_key=key, profile=profile).pk
            except AttemptConflictError:
                return "conflict"
        finally:
            connection.close()

    with monkeypatch.context() as patch:
        patch.setattr(QuerySet, "first", synchronized_first)
        executor = ThreadPoolExecutor(max_workers=2)
        try:
            futures = [executor.submit(submit, profile) for profile in profiles]
            results = [future.result(timeout=25) for future in futures]
        finally:
            barrier.abort()
            executor.shutdown(wait=True)
    pids = [observed.get_nowait(), observed.get_nowait()]
    assert pids[0] != pids[1] and observed.empty()
    committed = TransformationAttempt.objects.get(batch=case[2], attempt_key=key)
    assert TransformationAttempt.objects.filter(batch=case[2]).count() == 1
    assert results.count("conflict") == int(conflicting)
    assert all(value in (committed.pk, "conflict") for value in results)
    winner = results.index(committed.pk)
    assert committed.configuration["date_profile"]["name"] == profiles[winner].name
    assert _register(case, attempt_key=key, profile=profiles[winner]).pk == committed.pk
    if conflicting:
        before = _snapshot(committed)
        with pytest.raises(AttemptConflictError):
            _register(case, attempt_key=key, profile=profiles[1 - winner])
        assert _snapshot(committed) == before


def test_jsonb_null_result_payload_is_not_sql_null(make_case):
    case = make_case()
    completed = _complete(_running(case), case[3])
    row = completed.results.order_by("pk").first()
    with connections["default"].cursor() as cursor:
        cursor.execute(
            "UPDATE etl_transformationrecordresult "
            "SET result_payload = 'null'::jsonb WHERE id = %s",
            [row.pk],
        )
    before = _snapshot(completed)
    with pytest.raises(AttemptIntegrityError):
        _complete(completed, case[3])
    assert _snapshot(completed) == before
    with connections["default"].cursor() as cursor:
        cursor.execute(
            "SELECT result_payload IS NULL, result_payload::text "
            "FROM etl_transformationrecordresult WHERE id = %s",
            [row.pk],
        )
        assert cursor.fetchone() == (False, "null")
