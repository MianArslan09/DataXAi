"""PostgreSQL lease ownership/fencing on disposable synthetic staged batches."""

import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from queue import Queue
from threading import Barrier

import pytest
from django.db import OperationalError, connections, transaction
from django.db.models.query import QuerySet
from django.test.utils import CaptureQueriesContext

from etl import attempt_claims as claims
from etl import transformation_attempts as service
from etl.exceptions import AttemptConflictError, AttemptIntegrityError, AttemptRegistrationError
from etl.models import (
    StagingBatch,
    StagingIngestion,
    StagingRecord,
    TransformationAttempt,
    TransformationRecordResult,
)
from etl.tests.test_staging_constraints import staging_postgres as staging_postgres
from etl.tests.test_staging_persistence import application_postgres as application_postgres
from etl.tests.test_transformation_attempts import VERSION, _assert_no_writes, _register, _snapshot
from etl.tests.test_transformation_attempts import make_case as make_case


def _claim(attempt, ttl=300):
    return claims.claim_attempt(attempt_id=attempt.pk, ttl_seconds=ttl)


def _complete(attempt, case, claim=None):
    return service.complete_attempt(
        attempt_id=attempt.pk, result=case[3], transformation_version=VERSION, claim=claim
    )


def _fail(attempt, claim=None):
    return service.fail_attempt(
        attempt_id=attempt.pk, failure_code="SYNTHETIC", failure_stage="interpretation", claim=claim
    )


def _expire(attempt):
    with connections["default"].cursor() as cursor:
        cursor.execute(
            "UPDATE etl_transformationattempt SET claim_expires_at = "
            "clock_timestamp() - interval '1 second' WHERE id = %s",
            [attempt.pk],
        )


def _raw_snapshot():
    return tuple(
        list(model.objects.order_by("pk").values())
        for model in (StagingIngestion, StagingBatch, StagingRecord)
    )


def _identity(attempt):
    attempt.refresh_from_db()
    return tuple(
        getattr(attempt, name)
        for name in (
            "batch_id",
            "attempt_key",
            "configuration",
            "configuration_hash",
            "transformation_version",
            "registered_at",
        )
    )


def test_initial_claim_is_atomic_database_timed_and_opaque(make_case):
    case = make_case()
    attempt = _register(case)
    identity, raw = _identity(attempt), _raw_snapshot()
    with connections["default"].cursor() as cursor:
        cursor.execute("SELECT clock_timestamp()")
        before = cursor.fetchone()[0]
    with CaptureQueriesContext(connections["default"]) as queries:
        token = _claim(attempt, 3600)
    attempt.refresh_from_db()
    with connections["default"].cursor() as cursor:
        cursor.execute("SELECT clock_timestamp()")
        after = cursor.fetchone()[0]
    assert type(token) is claims.ClaimToken
    assert token.attempt_id == attempt.pk and token.generation == 1
    assert len(token.owner_token) == 32 and int(token.owner_token, 16) >= 0
    assert token.owner_token not in repr(token)
    with pytest.raises(FrozenInstanceError):
        token.generation = 2
    assert attempt.status == "RUNNING" and before <= attempt.started_at <= after
    assert (
        before + timedelta(seconds=3600)
        <= attempt.claim_expires_at
        <= after + timedelta(seconds=3600)
    )
    assert attempt.claim_owner == token.owner_token and attempt.claim_generation == 1
    sql = [query["sql"].upper() for query in queries]
    lock = next(i for i, query in enumerate(sql) if "FOR UPDATE" in query)
    clock = next(i for i, query in enumerate(sql) if "CLOCK_TIMESTAMP()" in query)
    assert lock < clock
    assert _identity(attempt) == identity and _raw_snapshot() == raw
    assert not attempt.results.exists()


@pytest.mark.parametrize("ttl", [True, False, 0, -1, 3601, 2**100, 1.5, "60", None])
def test_invalid_ttl_rejected_without_changes(make_case, ttl):
    attempt = _register(make_case())
    before = _snapshot(attempt)
    with pytest.raises(AttemptRegistrationError):
        _claim(attempt, ttl)
    assert _snapshot(attempt) == before
    token = _claim(attempt)
    before = _snapshot(attempt)
    with pytest.raises(AttemptRegistrationError):
        claims.renew_claim(claim=token, ttl_seconds=ttl)
    assert _snapshot(attempt) == before


@pytest.mark.parametrize("identity", [True, 0, -1, "1", None, 2**100])
def test_invalid_attempt_identity_rejected(identity):
    with pytest.raises(AttemptRegistrationError):
        claims.claim_attempt(attempt_id=identity, ttl_seconds=30)


def test_missing_attempt_and_unsupported_backend(make_case, monkeypatch):
    with pytest.raises(AttemptRegistrationError):
        claims.claim_attempt(attempt_id=2**63 - 1, ttl_seconds=30)
    attempt = _register(make_case())
    with monkeypatch.context() as patch:
        patch.setattr(connections["default"], "vendor", "sqlite")
        with pytest.raises(AttemptRegistrationError):
            _claim(attempt)


def test_renewal_never_shortens_and_keeps_owner_generation(make_case):
    attempt = _register(make_case())
    token = _claim(attempt, 300)
    attempt.refresh_from_db()
    original_expiry, started = attempt.claim_expires_at, attempt.started_at
    renewed = claims.renew_claim(claim=token, ttl_seconds=1)
    attempt.refresh_from_db()
    assert renewed == token and attempt.claim_expires_at == original_expiry
    renewed = claims.renew_claim(claim=token, ttl_seconds=3600)
    attempt.refresh_from_db()
    assert renewed == token and attempt.claim_expires_at > original_expiry
    assert (attempt.claim_owner, attempt.claim_generation, attempt.started_at) == (
        token.owner_token,
        token.generation,
        started,
    )
    before = _snapshot(attempt)
    with pytest.raises(AttemptConflictError):
        _claim(attempt)
    assert _snapshot(attempt) == before


def test_recovery_advances_generation_without_changing_source_or_identity(make_case):
    attempt = _register(make_case())
    old = _claim(attempt)
    identity, raw = _identity(attempt), _raw_snapshot()
    started = attempt.started_at
    _expire(attempt)
    replacement = _claim(attempt)
    attempt.refresh_from_db()
    assert replacement.generation == old.generation + 1
    assert replacement.owner_token != old.owner_token
    assert attempt.started_at == started and attempt.status == "RUNNING"
    assert _identity(attempt) == identity and _raw_snapshot() == raw
    before = _snapshot(attempt)
    with pytest.raises(AttemptConflictError):
        claims.renew_claim(claim=old, ttl_seconds=30)
    assert _snapshot(attempt) == before


@pytest.mark.parametrize(
    "damage",
    ["wrong_owner", "wrong_generation", "wrong_attempt", "expired", "missing", "superseded"],
)
def test_terminal_writes_and_renewal_are_fenced(make_case, damage):
    case = make_case()
    attempt = _register(case)
    token = _claim(attempt)
    if damage == "wrong_owner":
        token = replace(token, owner_token="0" * 32)
    elif damage == "wrong_generation":
        token = replace(token, generation=token.generation + 1)
    elif damage == "wrong_attempt":
        token = replace(token, attempt_id=_register(case).pk)
    elif damage == "expired":
        _expire(attempt)
    elif damage == "missing":
        token = None
    else:
        _expire(attempt)
        _claim(attempt)
    before, raw = _snapshot(attempt), _raw_snapshot()
    for action in (lambda: _complete(attempt, case, token), lambda: _fail(attempt, token)):
        with pytest.raises(AttemptConflictError):
            action()
        assert _snapshot(attempt) == before and _raw_snapshot() == raw
        assert not attempt.results.exists()
    if token is not None:
        with pytest.raises((AttemptConflictError, AttemptRegistrationError)):
            claims.renew_claim(claim=token, ttl_seconds=30)


@pytest.mark.parametrize("succeed", [True, False])
def test_terminal_cleanup_and_readonly_retry(make_case, succeed):
    case = make_case()
    attempt = _register(case)
    token = _claim(attempt)
    operation = (
        (lambda claim=None: _complete(attempt, case, claim))
        if succeed
        else (lambda claim=None: _fail(attempt, claim))
    )
    terminal = operation(token)
    assert terminal.claim_owner is None and terminal.claim_expires_at is None
    assert terminal.claim_generation == 1
    assert terminal.status == ("SUCCEEDED" if succeed else "FAILED")
    if succeed:
        assert (terminal.safe_row_count, terminal.unresolved_row_count) == (1, 2)
        assert terminal.results.count() == len(case[1])
        assert all(row.result_payload is None for row in terminal.results.all())
    else:
        assert terminal.safe_row_count is terminal.unresolved_row_count is None
        assert not terminal.results.exists()
    before = _snapshot(terminal)
    with CaptureQueriesContext(connections["default"]) as queries:
        assert operation().pk == terminal.pk
        assert operation(token).pk == terminal.pk
    _assert_no_writes(queries)
    assert _snapshot(terminal) == before
    for action in (
        lambda: _claim(attempt),
        lambda: claims.renew_claim(claim=token, ttl_seconds=30),
    ):
        with pytest.raises(AttemptConflictError):
            action()
    if not succeed:
        with pytest.raises(AttemptConflictError):
            service.fail_attempt(
                attempt_id=attempt.pk, failure_code="OTHER", failure_stage="interpretation"
            )
    assert _snapshot(terminal) == before


def test_legacy_running_is_not_taken_over(make_case):
    case = make_case()
    attempt = service.start_attempt(attempt_id=_register(case).pk)
    before = _snapshot(attempt)
    with pytest.raises(AttemptConflictError):
        _claim(attempt)
    assert _snapshot(attempt) == before
    assert _complete(attempt, case).status == "SUCCEEDED"


@pytest.mark.parametrize(
    "state", ["registered_managed", "running_unowned", "overflow", "unexpected_results"]
)
def test_corrupt_or_ineligible_claim_states_never_mutate(make_case, state):
    case = make_case()
    attempt = _register(case)
    token = None
    if state == "registered_managed":
        TransformationAttempt.objects.filter(pk=attempt.pk).update(claim_generation=1)
    else:
        token = _claim(attempt)
        if state == "running_unowned":
            TransformationAttempt.objects.filter(pk=attempt.pk).update(
                claim_owner=None, claim_expires_at=None
            )
        elif state == "overflow":
            TransformationAttempt.objects.filter(pk=attempt.pk).update(claim_generation=2**63 - 1)
            _expire(attempt)
        else:
            _expire(attempt)
            TransformationRecordResult.objects.create(
                batch=case[2],
                attempt=attempt,
                staged_record=case[2].records.first(),
                outcome="SAFE",
            )
    before = _snapshot(attempt)
    with pytest.raises(AttemptIntegrityError):
        _claim(attempt)
    if state == "registered_managed":
        with pytest.raises(AttemptIntegrityError):
            service.start_attempt(attempt_id=attempt.pk)
        with pytest.raises(AttemptIntegrityError):
            _fail(attempt)
    if state == "running_unowned":
        with pytest.raises(AttemptIntegrityError):
            _complete(attempt, case, token)
        with pytest.raises(AttemptIntegrityError):
            _fail(attempt, token)
    assert _snapshot(attempt) == before


@pytest.mark.parametrize("operation", ["claim", "renew", "reclaim", "complete", "fail"])
def test_outer_transaction_rollback_restores_attempt_and_results(make_case, operation):
    case = make_case()
    attempt = _register(case)
    token = None if operation == "claim" else _claim(attempt)
    if operation == "reclaim":
        _expire(attempt)
    before, raw = _snapshot(attempt), _raw_snapshot()
    with pytest.raises(RuntimeError, match="outer abort"):
        with transaction.atomic():
            if operation in {"claim", "reclaim"}:
                _claim(attempt)
            elif operation == "renew":
                claims.renew_claim(claim=token, ttl_seconds=3600)
            elif operation == "complete":
                _complete(attempt, case, token)
            else:
                _fail(attempt, token)
            raise RuntimeError("outer abort")
    assert _snapshot(attempt) == before and _raw_snapshot() == raw


def test_expiry_after_ledger_insert_rolls_back_entire_completion(make_case, monkeypatch):
    case = make_case()
    attempt = _register(case)
    token = _claim(attempt)
    before = _snapshot(attempt)
    original = service._verify_ledger
    observed = []

    def expire_after_verification(current, *args, **kwargs):
        original(current, *args, **kwargs)
        observed.append(current.results.count())
        _expire(current)
        current.refresh_from_db()

    monkeypatch.setattr(service, "_verify_ledger", expire_after_verification)
    with pytest.raises(AttemptConflictError):
        _complete(attempt, case, token)
    assert observed == [len(case[1])]
    assert _snapshot(attempt) == before


def _wait_blocked(pids):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with connections["default"].cursor() as cursor:
            cursor.execute(
                "SELECT count(DISTINCT pid) FROM pg_locks WHERE pid = ANY(%s) AND NOT granted",
                [pids],
            )
            if cursor.fetchone()[0] == len(pids):
                return
        time.sleep(0.01)  # Poll actual lock evidence, not an assumed scheduling delay.
    raise AssertionError("Workers did not reach the real PostgreSQL lock")


def _worker(action, pids, barrier=None):
    connection = connections["default"]
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET statement_timeout = '15s'")
            cursor.execute("SELECT pg_backend_pid()")
            pids.put(cursor.fetchone()[0])
        if barrier is not None:
            barrier.wait(timeout=10)
        try:
            return action()
        except AttemptConflictError:
            return "conflict"
    finally:
        connection.close()


@pytest.mark.parametrize("reclaim", [False, True])
def test_two_workers_compete_for_one_claim(make_case, reclaim):
    attempt = _register(make_case())
    if reclaim:
        _claim(attempt)
        _expire(attempt)
    pids, barrier = Queue(), Barrier(3)
    with ThreadPoolExecutor(max_workers=2) as pool:
        with transaction.atomic():
            TransformationAttempt.objects.select_for_update().get(pk=attempt.pk)
            futures = [
                pool.submit(_worker, lambda: _claim(attempt), pids, barrier) for _ in range(2)
            ]
            barrier.wait(timeout=10)
            backend_ids = [pids.get(timeout=5), pids.get(timeout=5)]
            assert backend_ids[0] != backend_ids[1]
            _wait_blocked(backend_ids)
        results = [future.result(timeout=20) for future in futures]
    assert results.count("conflict") == 1
    winner = next(result for result in results if result != "conflict")
    attempt.refresh_from_db()
    assert winner.generation == attempt.claim_generation == (2 if reclaim else 1)
    assert winner.owner_token == attempt.claim_owner
    assert not attempt.results.exists()


@pytest.mark.parametrize("recovery_first", [False, True])
def test_completion_and_recovery_serialize_both_lock_orderings(make_case, recovery_first):
    case = make_case()
    attempt = _register(case)
    token = _claim(attempt)
    if recovery_first:
        _expire(attempt)
    pids = Queue()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            TransformationAttempt.objects.select_for_update().get(pk=attempt.pk)
            second = (
                (lambda: _complete(attempt, case, token))
                if recovery_first
                else (lambda: _claim(attempt))
            )
            future = pool.submit(_worker, second, pids)
            _wait_blocked([pids.get(timeout=5)])
            if recovery_first:
                replacement = _claim(attempt)
                assert replacement.generation == 2
            else:
                assert _complete(attempt, case, token).status == "SUCCEEDED"
        assert future.result(timeout=20) == "conflict"
    attempt.refresh_from_db()
    assert attempt.status == ("RUNNING" if recovery_first else "SUCCEEDED")
    assert attempt.results.count() == (0 if recovery_first else len(case[1]))


@pytest.mark.parametrize("reclaim", [False, True])
def test_clock_is_read_after_lock_wait_not_transaction_start(make_case, reclaim):
    attempt = _register(make_case())
    token = _claim(attempt)
    pids = Queue()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            TransformationAttempt.objects.select_for_update().get(pk=attempt.pk)
            action = (
                (lambda: _claim(attempt))
                if reclaim
                else (lambda: claims.renew_claim(claim=token, ttl_seconds=30))
            )
            future = pool.submit(_worker, action, pids)
            _wait_blocked([pids.get(timeout=5)])
            # This timestamp is after the waiting worker's transaction began.
            # now() would incorrectly allow renewal; clock_timestamp() must reject it.
            with connections["default"].cursor() as cursor:
                cursor.execute(
                    "UPDATE etl_transformationattempt SET claim_expires_at = clock_timestamp() "
                    "WHERE id = %s",
                    [attempt.pk],
                )
        result = future.result(timeout=20)
        if reclaim:
            assert result.generation == 2
        else:
            assert result == "conflict"
    attempt.refresh_from_db()
    assert attempt.claim_generation == (2 if reclaim else 1)
    assert (attempt.claim_owner != token.owner_token) == reclaim


def test_unexpected_database_errors_propagate(make_case, monkeypatch):
    attempt = _register(make_case())

    def broken(*args, **kwargs):
        raise OperationalError("synthetic database interruption")

    monkeypatch.setattr(QuerySet, "get", broken)
    with pytest.raises(OperationalError, match="synthetic database interruption"):
        _claim(attempt)


@pytest.mark.parametrize(
    "damage", ["bool_id", "bool_generation", "owner_type", "owner_format", "dict"]
)
def test_malformed_tokens_rejected_without_leaking_credentials(make_case, damage):
    attempt = _register(make_case())
    token = _claim(attempt)
    if damage == "bool_id":
        bad = replace(token, attempt_id=True)
    elif damage == "bool_generation":
        bad = replace(token, generation=True)
    elif damage == "owner_type":
        bad = replace(token, owner_token=123)
    elif damage == "owner_format":
        bad = replace(token, owner_token="synthetic-sensitive-token")
    else:
        bad = {"attempt_id": attempt.pk, "owner_token": token.owner_token, "generation": 1}
    before = _snapshot(attempt)
    with pytest.raises(AttemptRegistrationError) as caught:
        claims.renew_claim(claim=bad, ttl_seconds=30)
    assert token.owner_token not in str(caught.value)
    assert "synthetic-sensitive-token" not in str(caught.value)
    assert _snapshot(attempt) == before


def test_failure_checks_expiry_again_before_terminal_write(make_case, monkeypatch):
    attempt = _register(make_case())
    token = _claim(attempt)
    before = _snapshot(attempt)
    original = service._no_results

    def expire_after_results_check(current):
        original(current)
        _expire(current)
        current.refresh_from_db()

    monkeypatch.setattr(service, "_no_results", expire_after_results_check)
    with pytest.raises(AttemptConflictError):
        _fail(attempt, token)
    assert _snapshot(attempt) == before


def test_managed_running_start_is_readonly_and_registered_owned_start_is_rejected(make_case):
    attempt = _register(make_case())
    token = _claim(attempt)
    before = _snapshot(attempt)
    with CaptureQueriesContext(connections["default"]) as queries:
        assert service.start_attempt(attempt_id=attempt.pk).pk == attempt.pk
    _assert_no_writes(queries)
    assert _snapshot(attempt) == before
    other = _register(make_case())
    with connections["default"].cursor() as cursor:
        cursor.execute(
            "UPDATE etl_transformationattempt SET claim_generation = 1, claim_owner = %s, "
            "claim_expires_at = clock_timestamp() + interval '5 minutes' WHERE id = %s",
            [token.owner_token, other.pk],
        )
    before = _snapshot(other)
    for action in (lambda: service.start_attempt(attempt_id=other.pk), lambda: _claim(other)):
        with pytest.raises(AttemptIntegrityError):
            action()
    assert _snapshot(other) == before


def test_completion_rereads_clock_at_unchanged_expiry_deadline(make_case, monkeypatch):
    """Synthetic elapsed time isolates the second clock read; the deadline never changes."""
    case = make_case()
    attempt = _register(case)
    token = _claim(attempt)
    attempt.refresh_from_db()
    deadline = attempt.claim_expires_at
    before = _snapshot(attempt)
    real_clock = claims._database_now
    observations = []

    def advancing_clock():
        observations.append(attempt.results.count())
        if len(observations) == 1:
            current = real_clock()
            assert current < deadline
            return current
        assert len(observations) == 2
        assert TransformationAttempt.objects.get(pk=attempt.pk).claim_expires_at == deadline
        return deadline

    monkeypatch.setattr(claims, "_database_now", advancing_clock)
    with pytest.raises(AttemptConflictError):
        _complete(attempt, case, token)
    assert observations == [0, len(case[1])]
    assert _snapshot(attempt) == before


@pytest.mark.parametrize(
    "operation, damage",
    [
        ("claim", "hash"),
        ("claim", "structure"),
        ("claim", "schema"),
        ("claim", "profile"),
        ("claim", "version"),
        ("renew", "hash"),
        ("reclaim", "hash"),
    ],
)
def test_claim_operations_reject_corrupt_configuration(make_case, operation, damage):
    """Configuration corruption must not acquire, extend or replace ownership."""
    attempt = _register(make_case())
    token = None if operation == "claim" else _claim(attempt)
    if operation == "reclaim":
        _expire(attempt)
    config = deepcopy(attempt.configuration)
    changes = {"configuration_hash": "0" * 64}
    if damage == "structure":
        changes = {"configuration": []}
    elif damage == "schema":
        config["schema"] = "unsupported-config-v0"
        changes = {"configuration": config}
    elif damage == "profile":
        config["date_profile"]["date_order"] = "unsupported-order"
        changes = {"configuration": config}
    elif damage == "version":
        changes = {"transformation_version": "invalid version"}
    TransformationAttempt.objects.filter(pk=attempt.pk).update(**changes)
    before, raw = deepcopy(_snapshot(attempt)), deepcopy(_raw_snapshot())
    with CaptureQueriesContext(connections["default"]) as queries:
        with pytest.raises(AttemptIntegrityError) as caught:
            if operation == "renew":
                claims.renew_claim(claim=token, ttl_seconds=3600)
            else:
                _claim(attempt)
    _assert_no_writes(queries)
    assert "FOR UPDATE" in " ".join(query["sql"].upper() for query in queries)
    assert "unsupported-order" not in str(caught.value)
    assert "invalid version" not in str(caught.value)
    assert _snapshot(attempt) == before
    assert _raw_snapshot() == raw


def test_valid_configuration_survives_claim_renew_recovery_and_completion(make_case):
    case = make_case()
    attempt = _register(case)
    identity, raw = deepcopy(_identity(attempt)), deepcopy(_raw_snapshot())
    first = _claim(attempt)
    assert claims.renew_claim(claim=first, ttl_seconds=3600) == first
    _expire(attempt)
    replacement = _claim(attempt)
    assert replacement.generation == first.generation + 1
    terminal = _complete(attempt, case, replacement)
    assert terminal.status == "SUCCEEDED"
    assert terminal.claim_owner is terminal.claim_expires_at is None
    assert terminal.claim_generation == replacement.generation
    assert (terminal.safe_row_count, terminal.unresolved_row_count) == (1, 2)
    assert _identity(terminal) == identity and _raw_snapshot() == raw
