"""Gate 1 unit tests: no database, source files, retries or service execution."""

import builtins
import json
import logging
import os
import time
import uuid
from dataclasses import FrozenInstanceError
from pathlib import Path

import psycopg
import pytest
import sqlalchemy
from django.db import InterfaceError, OperationalError
from django.db.backends.base.base import BaseDatabaseWrapper
from sqlalchemy.exc import OperationalError as SqlOperationalError

from etl.error_handling import (
    FailureCategory,
    FailureReason,
    RecoveryGuidance,
    classify_etl_failure,
)
from etl.exceptions import (
    AmbiguousColumnsError,
    AttemptConflictError,
    AttemptIntegrityError,
    AttemptRegistrationError,
    ETLError,
    MalformedSourceError,
    MissingColumnsError,
    SerializationError,
    SourceConnectionError,
    SourceNotFoundError,
    StagingBatchNotFoundError,
    StagingConflictError,
    StagingIngestionError,
    StagingIntegrityError,
    TypeConversionError,
)
from etl.source_snapshots import SnapshotError

SECRET = (
    "password=synthetic-secret postgresql://synthetic:secret@example.invalid/db "
    "C:\\private\\orders.csv customer-value-123"
)
MARKERS = ("synthetic-secret", "example.invalid", "private", "customer-value-123")
C = FailureCategory
R = RecoveryGuidance


def conversion_error():
    return TypeConversionError(
        source_name=SECRET,
        batch_number=7,
        column=SECRET,
        row_index=SECRET,
        bad_value=SECRET,
        reason=SECRET,
        cause=ValueError(SECRET),
    )


DOMAIN_CASES = [
    (ETLError, "etl_error", C.UNCLASSIFIED, R.OPERATOR_REVIEW),
    (SourceNotFoundError, "source_not_found", C.CONFIGURATION, R.OPERATOR_REVIEW),
    (MalformedSourceError, "malformed_source", C.INPUT, R.NO_UNCHANGED_RETRY),
    (MissingColumnsError, "missing_columns", C.INPUT, R.NO_UNCHANGED_RETRY),
    (AmbiguousColumnsError, "ambiguous_columns", C.INPUT, R.NO_UNCHANGED_RETRY),
    (SourceConnectionError, "source_connection_error", C.DATABASE, R.OPERATOR_REVIEW),
    (SerializationError, "serialization_error", C.INTEGRITY, R.OPERATOR_REVIEW),
    (StagingIngestionError, "staging_ingestion_error", C.UNCLASSIFIED, R.OPERATOR_REVIEW),
    (StagingConflictError, "staging_conflict", C.CONFLICT, R.OPERATOR_REVIEW),
    (StagingIntegrityError, "staging_integrity_error", C.INTEGRITY, R.OPERATOR_REVIEW),
    (StagingBatchNotFoundError, "staging_batch_not_found", C.CONFIGURATION, R.OPERATOR_REVIEW),
    (AttemptRegistrationError, "attempt_registration_error", C.UNCLASSIFIED, R.OPERATOR_REVIEW),
    (AttemptConflictError, "attempt_conflict", C.CONFLICT, R.OPERATOR_REVIEW),
    (AttemptIntegrityError, "attempt_integrity_error", C.INTEGRITY, R.OPERATOR_REVIEW),
    (TypeConversionError, "type_conversion_error", C.INPUT, R.NO_UNCHANGED_RETRY),
    (SnapshotError, "source_snapshot_error", C.UNCLASSIFIED, R.OPERATOR_REVIEW),
]


def assert_safe(failure):
    representations = (repr(failure), str(failure), json.dumps(failure.as_dict()))
    for representation in representations:
        assert all(marker not in representation for marker in MARKERS)
        assert len(representation.encode("utf-8")) < 1024
    assert all(type(value) is str for value in failure.as_dict().values())


@pytest.mark.parametrize("error,code,category,recovery", DOMAIN_CASES)
def test_existing_codes_and_conservative_domain_policy(error, code, category, recovery):
    exc = conversion_error() if error is TypeConversionError else error(SECRET)
    before = (exc.args, exc.message, exc.__cause__, exc.__traceback__, vars(exc).copy())

    failure = classify_etl_failure(exc)

    assert (failure.code, failure.category, failure.recovery) == (code, category, recovery)
    assert failure.reason == FailureReason.UNSPECIFIED
    assert classify_etl_failure(exc) == failure
    assert (exc.args, exc.message, exc.__cause__, exc.__traceback__, vars(exc)) == before
    assert_safe(failure)

    if error is not TypeConversionError:
        for constructor_args in ((), (None,), ("",)):
            original = error(*constructor_args)
            assert str(original) == error.default_message
            assert original.args == (error.default_message,)
            assert classify_etl_failure(original) == failure
        assert exc.args == (SECRET,) and exc.message == SECRET


def test_type_conversion_constructor_message_and_cause_are_preserved():
    cause = ValueError("original parse failure")
    exc = TypeConversionError(
        source_name="csv:orders.csv",
        batch_number=7,
        column="quantity",
        row_index=42,
        bad_value="12.5",
        reason="fractional quantity",
        cause=cause,
    )
    expected = (
        "csv:orders.csv batch 7, column 'quantity', row index 42: "
        "cannot convert '12.5' — fractional quantity"
    )
    assert str(exc) == exc.message == expected and exc.args == (expected,)
    assert exc.__cause__ is cause
    assert classify_etl_failure(exc).message == TypeConversionError.default_message
    original_error = conversion_error()
    assert original_error.__cause__ is not None
    assert_safe(classify_etl_failure(original_error))


@pytest.mark.parametrize(
    "exc,category,recovery",
    [
        (FileNotFoundError(2, SECRET, SECRET), C.SOURCE_IO, R.OPERATOR_REVIEW),
        (PermissionError(13, SECRET, SECRET), C.SOURCE_IO, R.OPERATOR_REVIEW),
        (OSError(5, SECRET, SECRET), C.SOURCE_IO, R.OPERATOR_REVIEW),
        (InterruptedError(SECRET), C.INTERRUPTED, R.RECONCILE_FIRST),
        (KeyboardInterrupt(SECRET), C.INTERRUPTED, R.RECONCILE_FIRST),
        (RuntimeError(SECRET), C.UNCLASSIFIED, R.OPERATOR_REVIEW),
        (ValueError(SECRET), C.UNCLASSIFIED, R.OPERATOR_REVIEW),
        (SystemExit(SECRET), C.UNCLASSIFIED, R.OPERATOR_REVIEW),
        (GeneratorExit(SECRET), C.UNCLASSIFIED, R.OPERATOR_REVIEW),
    ],
)
def test_non_domain_failures_do_not_invent_parser_or_retry_eligibility(exc, category, recovery):
    failure = classify_etl_failure(exc)
    assert (failure.code, failure.category, failure.recovery) == ("etl_error", category, recovery)
    assert_safe(failure)


def wrap_database(driver, wrapper):
    if wrapper == "driver":
        return driver
    if wrapper == "django":
        exc = OperationalError(SECRET)
        exc.__cause__ = driver
        return exc
    sql = SqlOperationalError(SECRET, {"password": SECRET}, driver)
    if wrapper == "sqlalchemy":
        return sql
    exc = SourceConnectionError(SECRET)
    exc.__cause__ = sql
    return exc


@pytest.mark.parametrize("wrapper", ["driver", "django", "sqlalchemy", "source"])
@pytest.mark.parametrize(
    "state,reason,category,recovery",
    [
        ("08003", FailureReason.CONNECTION_INTERRUPTED, C.DATABASE, R.RECONCILE_FIRST),
        ("08006", FailureReason.CONNECTION_INTERRUPTED, C.DATABASE, R.RECONCILE_FIRST),
        ("08007", FailureReason.CONNECTION_INTERRUPTED, C.DATABASE, R.RECONCILE_FIRST),
        ("57P01", FailureReason.CONNECTION_INTERRUPTED, C.DATABASE, R.RECONCILE_FIRST),
        ("57P02", FailureReason.CONNECTION_INTERRUPTED, C.DATABASE, R.RECONCILE_FIRST),
        ("57P03", FailureReason.CONNECTION_INTERRUPTED, C.DATABASE, R.RECONCILE_FIRST),
        ("40001", FailureReason.TRANSACTION_CONFLICT, C.DATABASE, R.RECONCILE_FIRST),
        ("40P01", FailureReason.TRANSACTION_CONFLICT, C.DATABASE, R.RECONCILE_FIRST),
        ("28000", FailureReason.DATABASE_CONFIGURATION, C.CONFIGURATION, R.OPERATOR_REVIEW),
        ("28P01", FailureReason.DATABASE_CONFIGURATION, C.CONFIGURATION, R.OPERATOR_REVIEW),
        ("42501", FailureReason.DATABASE_CONFIGURATION, C.CONFIGURATION, R.OPERATOR_REVIEW),
        ("42703", FailureReason.DATABASE_CONFIGURATION, C.CONFIGURATION, R.OPERATOR_REVIEW),
        ("42P01", FailureReason.DATABASE_CONFIGURATION, C.CONFIGURATION, R.OPERATOR_REVIEW),
    ],
)
def test_only_allowlisted_driver_codes_refine_known_database_failures(
    wrapper, state, reason, category, recovery
):
    driver = psycopg.errors.lookup(state)(SECRET)
    exc = wrap_database(driver, wrapper)
    before = (exc.args, exc.__cause__, vars(exc).copy())

    failure = classify_etl_failure(exc)

    assert failure.code == ("source_connection_error" if wrapper == "source" else "etl_error")
    assert (failure.reason, failure.category, failure.recovery) == (reason, category, recovery)
    assert (exc.args, exc.__cause__, vars(exc)) == before
    assert_safe(failure)


@pytest.mark.parametrize(
    "state", [None, "99999", "08ZZZ", pytest.param(SECRET * 1000, id="oversized-code"), [], 8006]
)
def test_unknown_or_invalid_driver_code_is_never_assumed_transient(state):
    driver = psycopg.OperationalError(SECRET)
    driver.sqlstate = state
    failure = classify_etl_failure(wrap_database(driver, "source"))
    assert failure.reason == FailureReason.UNSPECIFIED
    assert failure.recovery == R.OPERATOR_REVIEW
    assert_safe(failure)


def test_absent_cause_interface_errors_and_connection_flag_are_conservative():
    errors = [
        OperationalError(SECRET),
        InterfaceError(SECRET),
        psycopg.InterfaceError(SECRET),
        SourceConnectionError(SECRET),
        SqlOperationalError(SECRET, None, ValueError(SECRET), connection_invalidated=True),
    ]
    for exc in errors:
        failure = classify_etl_failure(exc)
        assert failure.category == C.DATABASE
        assert failure.reason == FailureReason.UNSPECIFIED
        assert failure.recovery == R.OPERATOR_REVIEW
        assert_safe(failure)


def test_unknown_exception_text_metadata_and_chains_are_not_inspected():
    class Unknown(Exception):
        def __str__(self):
            pytest.fail("Unknown exception text was evaluated")

        def __repr__(self):
            pytest.fail("Unknown exception representation was evaluated")

        @property
        def code(self):
            pytest.fail("Unknown exception code was evaluated")

        @property
        def sqlstate(self):
            pytest.fail("Unknown driver metadata was evaluated")

    exc = Unknown(SECRET)
    exc.__cause__ = psycopg.errors.ConnectionFailure(SECRET)
    try:
        raise exc
    except Unknown as caught:
        caught.add_note(SECRET)
        failure = classify_etl_failure(caught)
        assert caught.__traceback__ is not None
    assert failure.code == "etl_error"
    assert failure.category == C.UNCLASSIFIED
    assert failure.recovery == R.OPERATOR_REVIEW
    assert_safe(failure)


def test_overridden_domain_code_and_message_cannot_enter_safe_projection():
    class CustomFailure(MalformedSourceError):
        code = SECRET
        default_message = SECRET

        def __str__(self):
            pytest.fail("Domain exception text was evaluated")

    exc = CustomFailure(SECRET)
    exc.code = SECRET
    exc.reason = SECRET
    exc.failure_diagnostics = {"password": SECRET}
    failure = classify_etl_failure(exc)
    assert failure.code == MalformedSourceError.code
    assert failure.message == MalformedSourceError.default_message
    assert_safe(failure)


def test_implicit_context_is_not_database_recovery_evidence():
    exc = OperationalError(SECRET)
    exc.__context__ = psycopg.errors.ConnectionFailure(SECRET)
    failure = classify_etl_failure(exc)
    assert failure.reason == FailureReason.UNSPECIFIED
    assert failure.recovery == R.OPERATOR_REVIEW


def test_cyclic_and_unrecognized_explicit_wrappers_stop_conservatively():
    cyclic = OperationalError(SECRET)
    cyclic.__cause__ = cyclic
    spoofed = RuntimeError(SECRET)
    spoofed.sqlstate = "08006"
    for cause in (cyclic, spoofed):
        exc = SourceConnectionError(SECRET)
        exc.__cause__ = cause
        failure = classify_etl_failure(exc)
        assert failure.reason == FailureReason.UNSPECIFIED
        assert failure.recovery == R.OPERATOR_REVIEW
        assert_safe(failure)


@pytest.mark.parametrize(
    "message",
    ["The claim has expired.", "An active claim cannot be replaced.", "FAILED is terminal."],
)
def test_claim_messages_cannot_authorize_recovery(message):
    failure = classify_etl_failure(AttemptConflictError(message))
    assert failure.category == C.CONFLICT
    assert failure.recovery == R.OPERATOR_REVIEW
    assert failure.reason == FailureReason.UNSPECIFIED


def test_projection_is_immutable_deterministic_and_detached():
    exc = conversion_error()
    failure = classify_etl_failure(exc)
    with pytest.raises(FrozenInstanceError):
        failure.message = SECRET
    output = failure.as_dict()
    output["message"] = SECRET
    assert failure.as_dict()["message"] == TypeConversionError.default_message
    assert classify_etl_failure(exc).as_dict() == failure.as_dict()
    assert_safe(failure)


def test_classification_and_projection_have_no_operational_side_effects(monkeypatch):
    errors = [
        conversion_error(),
        SnapshotError(SECRET),
        wrap_database(psycopg.errors.ConnectionFailure(SECRET), "source"),
        OSError(5, SECRET, SECRET),
        KeyboardInterrupt(SECRET),
        RuntimeError(SECRET),
    ]

    def forbidden(*args, **kwargs):
        pytest.fail("Classification attempted an operational side effect")

    for target, attribute in (
        (builtins, "open"),
        (os, "open"),
        (Path, "open"),
        (logging.Logger, "_log"),
        (BaseDatabaseWrapper, "cursor"),
        (psycopg, "connect"),
        (sqlalchemy, "create_engine"),
        (time, "sleep"),
        (time, "time"),
        (uuid, "uuid4"),
    ):
        monkeypatch.setattr(target, attribute, forbidden)
    for exc in errors:
        assert classify_etl_failure(exc).as_dict() == classify_etl_failure(exc).as_dict()


@pytest.mark.parametrize("value", [None, SECRET, {"message": SECRET}])
def test_invalid_classifier_argument_uses_fixed_error_text(value):
    with pytest.raises(TypeError, match="^Expected an exception instance\\.$"):
        classify_etl_failure(value)
