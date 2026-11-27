"""Gate 3 observations with blocked ORM and deterministic collaborators, no DB server."""

import io
import json
import logging
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID, uuid4

import pandas as pd
import psycopg
import pytest
from sqlalchemy.exc import ProgrammingError

from etl import _coordinator_diagnostics as diagnostics
from etl import ingestion_completion as bound
from etl import ingestion_runs as ingestion
from etl import pipeline_runs as pipeline
from etl._coordinator_diagnostics import CoordinatorDiagnostic
from etl.error_handling import classify_etl_failure
from etl.exceptions import (
    AttemptConflictError,
    ETLError,
    StagingIntegrityError,
    TypeConversionError,
)
from etl.models import PipelineRun
from etl.source_snapshots import SnapshotError
from etl.sources import csv_source, excel_source, postgres_source
from etl.sources.contract import CANONICAL_COLUMNS, SourceBatch
from etl.transforms.date_contract import SourceDateProfile

SECRET = "password=secret /private/orders.csv PrivateSheet private_table SELECT private_value"


def events(caplog):
    return [
        record
        for record in caplog.records
        if isinstance(record.msg, str) and record.msg.startswith("etl.coordinator_failure ")
    ]


def one_event(caplog, stage, operation=None):
    (record,) = events(caplog)
    assert record.etl["stage"] == stage
    if operation is not None:
        assert record.etl["operation"] == operation
    assert record.args == () and record.exc_info is None and record.stack_info is None
    assert SECRET not in record.getMessage()
    assert json.loads(record.getMessage().partition(" ")[2]) == record.etl
    return record.etl


@pytest.fixture
def rig(monkeypatch):
    """Execute real coordinator control flow with no database access or real transactions."""
    options = dict(
        ingestion_id=uuid4(),
        attempt_key=uuid4(),
        profile=SourceDateProfile(),
        transformation_version="synthetic-v1",
        claim_ttl_seconds=60,
    )
    batch = SourceBatch(pd.DataFrame([[1] * 8], columns=CANONICAL_COLUMNS), "csv:orders.csv", 0)
    attempt = SimpleNamespace(
        pk=11,
        status="REGISTERED",
        batch=SimpleNamespace(row_count=1),
        results=Mock(),
        failure_code="TYPE_CONVERSION_ERROR",
        failure_stage="INTERPRETATION",
        failure_diagnostics={"input_rows": 1},
        refresh_from_db=Mock(),
    )
    attempt.results.using.return_value.exists.return_value = False
    run = PipelineRun(id=21, attempt_id=11, status="pending", records_ingested=1)
    state = SimpleNamespace(
        options=options,
        batch=batch,
        attempt=attempt,
        run=run,
        active=False,
        commits=0,
        commit_failure=None,
    )

    @contextmanager
    def atomic(**kwargs):
        assert not state.active
        state.active = True
        try:
            yield
            if (
                state.commit_failure is not None
                and state.commits >= 1
                and attempt.status in ("SUCCEEDED", "FAILED")
            ):
                raise state.commit_failure
            state.commits += 1
        finally:
            state.active = False

    def claim(**kwargs):
        attempt.status = "RUNNING"
        return SimpleNamespace(owner=SECRET)

    def finish(status):
        def apply(**kwargs):
            attempt.status = status
            return attempt

        return apply

    def project(value):
        run.status = value.status.lower()
        return run

    monkeypatch.setattr(pipeline, "_check_entry", Mock())
    monkeypatch.setattr(pipeline.transaction, "atomic", atomic)
    monkeypatch.setattr(pipeline, "register_attempt", Mock(return_value=attempt))
    monkeypatch.setattr(pipeline, "_check_batch_count", Mock())
    monkeypatch.setattr(pipeline, "_validate_claim_state", Mock())
    monkeypatch.setattr(pipeline, "_locked", Mock(return_value=attempt))
    monkeypatch.setattr(pipeline, "_project", project)
    monkeypatch.setattr(pipeline, "claim_attempt", claim)
    monkeypatch.setattr(pipeline, "renew_claim", Mock())
    monkeypatch.setattr(pipeline, "load_source_batch", Mock(return_value=batch))
    monkeypatch.setattr(pipeline, "AliasMapper", lambda: SimpleNamespace(transform=lambda x: x))
    monkeypatch.setattr(pipeline, "DateNormalizer", lambda p: SimpleNamespace(apply=lambda x: x))
    monkeypatch.setattr(pipeline, "complete_attempt", finish("SUCCEEDED"))
    monkeypatch.setattr(pipeline, "fail_attempt", finish("FAILED"))
    monkeypatch.setattr(
        pipeline, "stage_source_batch", Mock(return_value=SimpleNamespace(batch_number=0))
    )
    return state


@pytest.mark.parametrize(
    "point,stage",
    [
        ("_check_entry", "admission"),
        ("stage_source_batch", "staging"),
        ("register_attempt", "admission"),
        ("claim_attempt", "claiming"),
        ("load_source_batch", "reconstruction"),
        ("renew_claim", "claiming"),
        ("complete_attempt", "finalization"),
    ],
)
def test_nested_pipeline_failure_stage_and_original_exception(
    rig, monkeypatch, caplog, point, stage
):
    original = RuntimeError(SECRET)
    monkeypatch.setattr(pipeline, point, Mock(side_effect=original))
    with pytest.raises(RuntimeError) as caught:
        pipeline.stage_and_run_batch(**rig.options, batch=rig.batch)
    assert caught.value is original and not rig.active
    data = one_event(caplog, stage)
    assert data["ingestion_id"] == str(rig.options["ingestion_id"])
    assert data["execution_key"] == str(rig.options["attempt_key"])
    assert data["category"] == "unclassified" and data["recovery"] == "operator_review"
    if point in ("claim_attempt", "load_source_batch", "renew_claim", "complete_attempt"):
        assert data["attempt_id"] == 11 and data["pipeline_run_id"] == 21


def test_interpretation_failure_is_not_confused_with_claiming(rig, monkeypatch, caplog):
    original = KeyboardInterrupt(SECRET)
    monkeypatch.setattr(
        pipeline, "AliasMapper", lambda: SimpleNamespace(transform=Mock(side_effect=original))
    )
    with pytest.raises(KeyboardInterrupt) as caught:
        pipeline.run_staged_batch(**rig.options, batch_number=0)
    assert caught.value is original
    assert one_event(caplog, "interpretation")["category"] == "interrupted"


def conversion_error():
    return TypeConversionError(
        source_name=SECRET,
        batch_number=0,
        column=SECRET,
        row_index=SECRET,
        bad_value=SECRET,
        reason=SECRET,
    )


@pytest.mark.parametrize("existing", [False, True])
def test_failed_result_is_observed_after_transaction_exit(rig, monkeypatch, caplog, existing):
    if existing:
        rig.attempt.status = "FAILED"
    else:
        monkeypatch.setattr(
            pipeline,
            "DateNormalizer",
            lambda p: SimpleNamespace(apply=Mock(side_effect=conversion_error())),
        )
    original_log = pipeline.logger.log

    def capture(*args, **kwargs):
        assert not rig.active
        return original_log(*args, **kwargs)

    monkeypatch.setattr(pipeline.logger, "log", capture)
    run = pipeline.run_staged_batch(**rig.options, batch_number=0)
    assert run is rig.run and run.status == "failed"
    data = one_event(caplog, "finalization")
    assert data["observation"] == "failed_result" and data["reason"] == "terminal_attempt_failed"
    assert data["recovery"] == "operator_review"


def test_failed_result_commit_error_remains_database_failure(rig, monkeypatch, caplog):
    rig.attempt.status = "FAILED"
    original = psycopg.errors.ConnectionFailure(SECRET)
    rig.commit_failure = original
    with pytest.raises(psycopg.errors.ConnectionFailure) as caught:
        pipeline.run_staged_batch(**rig.options, batch_number=0)
    assert caught.value is original
    data = one_event(caplog, "finalization")
    assert data["observation"] == "exception" and data["reason"] == "connection_interrupted"
    assert data["recovery"] == "reconcile_first"


@pytest.mark.parametrize("sink_error", [RuntimeError, KeyboardInterrupt])
@pytest.mark.parametrize("outcome", ["exception", "failed", "succeeded"])
def test_sink_failure_cannot_change_pipeline_outcome(rig, monkeypatch, sink_error, outcome):
    monkeypatch.setattr(pipeline.logger, "log", Mock(side_effect=sink_error(SECRET)))
    if outcome == "exception":
        original = AttemptConflictError(SECRET)
        monkeypatch.setattr(pipeline, "claim_attempt", Mock(side_effect=original))
        with pytest.raises(AttemptConflictError) as caught:
            pipeline.run_staged_batch(**rig.options, batch_number=0)
        assert caught.value is original
    else:
        if outcome == "failed":
            rig.attempt.status = "FAILED"
        result = pipeline.run_staged_batch(**rig.options, batch_number=0)
        assert result is rig.run and result.status == outcome


def test_projection_never_formats_unknown_error_or_unsafe_identity(caplog):
    class OpaqueError(Exception):
        def __str__(self):
            pytest.fail("Raw error formatted")

        def __repr__(self):
            pytest.fail("Raw error represented")

    original = OpaqueError(SECRET)
    with pytest.raises(OpaqueError) as caught:
        with CoordinatorDiagnostic(pipeline.logger, SECRET, SECRET, SECRET, True) as diagnostic:
            diagnostic.stage = SECRET
            diagnostic.attempt_id = SECRET
            diagnostic.pipeline_run_id = -1
            raise original
    assert caught.value is original
    data = one_event(caplog, "unknown", "unknown")
    assert set(data) == {
        "operation",
        "stage",
        "observation",
        "code",
        "category",
        "reason",
        "message",
        "recovery",
    }


class Rows:
    def __init__(self, values=(), failure=None):
        self.values = iter(values)
        self.failure = failure
        self.close = Mock()

    def __iter__(self):
        return self

    def __next__(self):
        try:
            return next(self.values)
        except StopIteration:
            if self.failure is not None:
                raise self.failure from None
            raise


@pytest.fixture
def live(rig, monkeypatch):
    batches = Mock()
    batches.using.return_value = batches
    batches.exists.return_value = False
    batches.count.return_value = 1
    batches.filter.return_value = batches
    parent = SimpleNamespace(source_kind="CSV", status="OPEN", batches=batches)
    monkeypatch.setattr(ingestion, "_request", lambda *args: (parent, rig.options))
    source = csv_source.CsvSource("orders.csv")
    rows = Rows([rig.batch.data])
    monkeypatch.setattr(csv_source.pd, "read_csv", Mock(return_value=rows))
    monkeypatch.setattr(
        ingestion.IngestionExecutionSummary,
        "_record",
        lambda self, n, run: replace(self, successful_batches=self.successful_batches + 1),
    )
    options = dict(rig.options)
    options["execution_key"] = options.pop("attempt_key")
    return SimpleNamespace(source=source, rows=rows, options=options, parent=parent)


@pytest.mark.parametrize("mode", ["live", "resume"])
def test_nested_ingestion_does_not_duplicate_child_error(rig, live, monkeypatch, caplog, mode):
    original = AttemptConflictError(SECRET)
    monkeypatch.setattr(pipeline, "claim_attempt", Mock(side_effect=original))
    with pytest.raises(AttemptConflictError) as caught:
        if mode == "live":
            ingestion.run_ingestion(**live.options, source=live.source)
        else:
            ingestion.resume_staged_range(**live.options, first_batch=0, last_batch=0)
    assert caught.value is original
    one_event(caplog, "claiming", "run_staged_batch")


@pytest.mark.parametrize("point", ["open", "iteration", "cleanup"])
def test_source_failure_stage_and_no_stale_batch_identity(rig, live, monkeypatch, caplog, point):
    original = OSError(SECRET)
    if point == "open":
        monkeypatch.setattr(csv_source.pd, "read_csv", Mock(side_effect=original))
    elif point == "iteration":
        live.rows.failure = original
        live.rows.close.side_effect = RuntimeError("secondary cleanup")
    else:
        live.rows.close.side_effect = original
    with pytest.raises(OSError) as caught:
        ingestion.run_ingestion(**live.options, source=live.source)
    assert caught.value is original
    data = one_event(caplog, "source_cleanup" if point == "cleanup" else "source_read")
    assert "batch_number" not in data and "pipeline_run_id" not in data
    assert data["category"] == "source_io"


@pytest.mark.parametrize("state", ["28P01", "42P01", "42501", "40001"])
@pytest.mark.parametrize("point", ["open", "iteration"])
def test_database_adapter_classification_reaches_coordinator(
    rig, live, monkeypatch, caplog, state, point
):
    driver = psycopg.errors.lookup(state)(SECRET)
    original = ProgrammingError(SECRET, {"secret": SECRET}, driver)
    connection = Mock()
    connection.execution_options.return_value = connection
    engine = Mock()
    engine.connect.return_value = connection
    monkeypatch.setattr(postgres_source, "create_engine", Mock(return_value=engine))
    if point == "open":
        connection.execute.side_effect = original
    else:
        monkeypatch.setattr(postgres_source.pd, "read_sql", Mock(side_effect=original))
    source = postgres_source.PostgresSource(SECRET, table=SECRET)
    live.parent.source_kind = "POSTGRES"
    with pytest.raises(ETLError) as caught:
        ingestion.run_ingestion(**live.options, source=source)
    assert type(caught.value) is ETLError and caught.value.__cause__ is original
    data = one_event(caplog, "source_read", "run_ingestion")
    assert {
        k: data[k] for k in classify_etl_failure(caught.value).as_dict()
    } == classify_etl_failure(caught.value).as_dict()
    assert data["reason"] == (
        "transaction_conflict" if state == "40001" else "database_configuration"
    )
    connection.close.assert_called_once()
    engine.dispose.assert_called_once()


@pytest.fixture
def completion(rig, monkeypatch):
    parent = SimpleNamespace(status="OPEN")
    proof = SimpleNamespace(
        ingestion=parent,
        execution_key=rig.options["attempt_key"],
        sealed_at=None,
        binding_digest="a" * 64,
        transformation_version="synthetic-v1",
    )
    monkeypatch.setattr(bound, "_check_entry", Mock())
    monkeypatch.setattr(
        bound.IngestionCompletionProof.objects,
        "select_related",
        Mock(return_value=SimpleNamespace(get=Mock(return_value=proof))),
    )
    monkeypatch.setattr(
        bound,
        "_verify_binding",
        Mock(return_value=(object(), bound.CsvScope("orders.csv", 1), rig.options["profile"])),
    )
    traversal = SimpleNamespace(
        verified_exhausted=True, precheck_seconds=0.0, postcheck_seconds=0.0
    )

    class Parser:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def __iter__(self):
            yield rig.batch

        def __getattr__(self, key):
            return getattr(traversal, key)

    monkeypatch.setattr(bound, "open_snapshot_csv", Mock(return_value=Parser()))
    monkeypatch.setattr(
        bound, "_stage_with_digest", Mock(return_value=(SimpleNamespace(row_count=1), "a" * 64))
    )
    seal = Mock(return_value=proof)
    monkeypatch.setattr(bound, "_seal", seal)
    return SimpleNamespace(proof=proof, parent=parent, traversal=traversal, seal=seal)


def complete(rig):
    return bound.complete_bound_ingestion(
        ingestion_id=rig.options["ingestion_id"], claim_ttl_seconds=60
    )


@pytest.mark.parametrize(
    "point,stage",
    [
        ("open_snapshot_csv", "source_read"),
        ("_stage_with_digest", "staging"),
        ("_seal", "sealing"),
    ],
)
def test_bound_failure_never_returns_receipt(rig, completion, monkeypatch, caplog, point, stage):
    original = SnapshotError(SECRET)
    monkeypatch.setattr(bound, point, Mock(side_effect=original))
    with pytest.raises(SnapshotError) as caught:
        complete(rig)
    assert caught.value is original and completion.proof.sealed_at is None
    one_event(caplog, stage, "complete_bound_ingestion")
    if point != "_seal":
        completion.seal.assert_not_called()


def test_bound_nested_error_has_one_primary_event(rig, completion, monkeypatch, caplog):
    original = AttemptConflictError(SECRET)
    monkeypatch.setattr(pipeline, "claim_attempt", Mock(side_effect=original))
    with pytest.raises(AttemptConflictError) as caught:
        complete(rig)
    assert caught.value is original
    one_event(caplog, "claiming", "run_staged_batch")
    completion.seal.assert_not_called()


@pytest.mark.parametrize("existing", [False, True])
def test_bound_failed_attempt_is_distinct_and_cannot_seal(
    rig, completion, monkeypatch, caplog, existing
):
    if existing:
        rig.attempt.status = "FAILED"
    else:
        monkeypatch.setattr(
            pipeline,
            "DateNormalizer",
            lambda p: SimpleNamespace(apply=Mock(side_effect=conversion_error())),
        )
    with pytest.raises(StagingIntegrityError) as caught:
        complete(rig)
    assert type(caught.value) is StagingIntegrityError
    assert str(caught.value) == "Bound execution did not successfully interpret the staged batch."
    data = one_event(caplog, "finalization", "complete_bound_ingestion")
    assert data["code"] == "staging_integrity_error" and data["reason"] == "terminal_attempt_failed"
    assert data["category"] == "conflict" and data["attempt_id"] == 11
    assert rig.attempt.status == "FAILED" and completion.parent.status == "OPEN"
    completion.seal.assert_not_called()


def test_unverified_exhaustion_is_not_failed_attempt(rig, completion, caplog):
    completion.traversal.verified_exhausted = False
    with pytest.raises(StagingIntegrityError):
        complete(rig)
    data = one_event(caplog, "source_read")
    assert data["reason"] == "unspecified" and data["category"] == "integrity"
    completion.seal.assert_not_called()


def test_receipt_ack_failure_is_distinct_and_does_not_open_source(rig, completion, caplog):
    completion.parent.status = "COMPLETE"
    original = StagingIntegrityError(SECRET)
    completion.seal.side_effect = original
    with pytest.raises(StagingIntegrityError) as caught:
        complete(rig)
    assert caught.value is original
    one_event(caplog, "acknowledgment")
    bound.open_snapshot_csv.assert_not_called()


def test_completion_timing_sink_failure_does_not_block_sealing(rig, completion, monkeypatch):
    monkeypatch.setattr(bound.logger, "log", Mock(side_effect=RuntimeError(SECRET)))
    assert complete(rig) is completion.proof
    completion.seal.assert_called_once()


@pytest.mark.parametrize("kind", ["csv", "excel", "postgres", "empty_excel"])
def test_inherited_source_sink_cannot_break_processing(monkeypatch, kind):
    """A logger handler raising here formerly interrupted successful source traversal."""
    frame = pd.DataFrame([[1] * 8], columns=CANONICAL_COLUMNS)
    if kind == "csv":
        module = csv_source
        source = module.CsvSource("private.csv")
        source._reader = Rows([frame])
    elif kind in ("excel", "empty_excel"):
        module = excel_source
        source = module.ExcelSource("private.xlsx")
        rows = Rows([] if kind == "empty_excel" else [CANONICAL_COLUMNS, [1] * 8])
        source.sheet_names = ["private_sheet"]
        source._workbook = {"private_sheet": SimpleNamespace(iter_rows=lambda **kwargs: rows)}
    else:
        module = postgres_source
        source = module.PostgresSource(SECRET)
        source._connection = Mock()
        monkeypatch.setattr(module.pd, "read_sql", Mock(return_value=Rows([frame])))
    monkeypatch.setattr(module.logger, "isEnabledFor", lambda level: True)
    monkeypatch.setattr(module.logger, "handle", Mock(side_effect=RuntimeError(SECRET)))
    batches = list(source.extract())
    assert len(batches) == (0 if kind == "empty_excel" else 1)
    if batches:
        pd.testing.assert_frame_equal(batches[0].data, frame)


@pytest.mark.parametrize("fault", ["capture", "binding"])
def test_binding_failure_stage_is_safe(rig, monkeypatch, caplog, fault):
    original = SnapshotError(SECRET)
    monkeypatch.setattr(
        bound,
        "connections",
        {
            "default": SimpleNamespace(
                vendor="postgresql", in_atomic_block=False, get_autocommit=lambda: True
            )
        },
    )
    monkeypatch.setattr(
        bound, "capture_snapshot", Mock(return_value=SimpleNamespace(sha256="a" * 64, size_bytes=1))
    )
    if fault == "capture":
        bound.capture_snapshot.side_effect = original
    else:
        monkeypatch.setattr(bound, "_locked_ingestion", Mock(side_effect=original))
    with pytest.raises(SnapshotError) as caught:
        bound.bind_file_ingestion(
            ingestion_id=rig.options["ingestion_id"],
            input_path=SECRET,
            scope=bound.CsvScope("orders.csv", 1),
            execution_key=rig.options["attempt_key"],
            profile=rig.options["profile"],
            transformation_version="synthetic-v1",
        )
    assert caught.value is original
    one_event(caplog, "snapshot_capture" if fault == "capture" else "binding")


def test_seal_returns_receipt_when_post_transaction_timing_sink_fails(monkeypatch):
    """Only checks diagnostic isolation; real atomicity is in the owner-run PG tests."""
    from contextlib import nullcontext

    parent = SimpleNamespace(
        status="COMPLETE", completed_at="stamp", final_batch_count=1, final_row_count=1
    )
    proof = SimpleNamespace(
        binding_digest="binding",
        sealed_at="stamp",
        receipt_digest="receipt",
        source_batches=1,
        source_rows=1,
        manifest_digest="manifest",
        coverage={},
    )
    monkeypatch.setattr(bound.transaction, "atomic", lambda **kwargs: nullcontext())
    monkeypatch.setattr(bound, "_locked_ingestion", lambda identity: parent)
    monkeypatch.setattr(
        bound.IngestionCompletionProof.objects,
        "select_for_update",
        lambda: SimpleNamespace(get=lambda **kwargs: proof),
    )
    monkeypatch.setattr(bound, "_verify_binding", lambda *args: (None, None, None))
    monkeypatch.setattr(bound, "_receipt_digest", lambda p: "receipt")
    monkeypatch.setattr(bound, "_reconcile", lambda *args: {})
    monkeypatch.setattr(bound.logger, "log", Mock(side_effect=KeyboardInterrupt(SECRET)))
    assert bound._seal(uuid4(), "binding", None) is proof


def test_failed_condition_does_not_misclassify_another_exception(caplog):
    original = OSError(SECRET)
    with pytest.raises(OSError) as caught:
        with CoordinatorDiagnostic(bound.logger, "complete_bound_ingestion", uuid4()) as diagnostic:
            diagnostic.stage = "source_cleanup"
            diagnostic.failed_result(error=StagingIntegrityError())
            raise original
    assert caught.value is original
    data = one_event(caplog, "source_cleanup")
    assert data["category"] == "source_io" and data["reason"] == "unspecified"


@pytest.mark.parametrize("point", ["open", "iteration"])
def test_csv_parser_classification_reaches_live_coordinator(rig, live, monkeypatch, caplog, point):
    original = pd.errors.ParserError(SECRET)
    if point == "open":
        monkeypatch.setattr(csv_source.pd, "read_csv", Mock(side_effect=original))
    else:
        live.rows.failure = original
    from etl.exceptions import MalformedSourceError

    with pytest.raises(MalformedSourceError) as caught:
        ingestion.run_ingestion(**live.options, source=live.source)
    assert caught.value.__cause__ is original
    data = one_event(caplog, "source_read")
    assert data["code"] == "malformed_source" and data["category"] == "input"
    assert data["recovery"] == "no_unchanged_retry"


def test_run_identity_observation_never_loads_deferred_or_related_fields(caplog):
    class DeferredRun:
        def __getattr__(self, name):
            pytest.fail("Diagnostic tried to resolve a deferred or related field")

    with pytest.raises(RuntimeError):
        with CoordinatorDiagnostic(pipeline.logger, "run_staged_batch", uuid4()) as diagnostic:
            diagnostic.record_run(DeferredRun())
            raise RuntimeError(SECRET)
    data = one_event(caplog, "admission")
    assert "pipeline_run_id" not in data and "attempt_id" not in data


@pytest.mark.parametrize("format_string", ["%(message)s", "%(levelname)s %(message)s"])
def test_ordinary_stream_handler_renders_only_safe_projection(format_string):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter(format_string))
    records = []
    handler.addFilter(lambda record: records.append(record) or True)
    logger = logging.Logger("etl.synthetic_rendering", logging.WARNING)
    logger.addHandler(handler)
    original = StagingIntegrityError(SECRET)
    cause = RuntimeError(SECRET)
    try:
        with pytest.raises(StagingIntegrityError) as caught:
            with CoordinatorDiagnostic(
                logger, "stage_and_run_batch", UUID(int=1), UUID(int=2), 0
            ) as diagnostic:
                diagnostic.stage = "staging"
                diagnostic.record_run(SimpleNamespace(id=21, attempt_id=11))
                diagnostic.untrusted_metadata = {"path": SECRET, "claim_owner": SECRET}
                raise original from cause
        assert caught.value is original and original.__cause__ is cause
        rendered = stream.getvalue()
        assert len(rendered.splitlines()) == 1
        prefix, marker, payload = rendered.partition("etl.coordinator_failure ")
        assert marker == "etl.coordinator_failure "
        assert prefix == ("" if format_string == "%(message)s" else "WARNING ")
        expected = {
            "operation": "stage_and_run_batch",
            "stage": "staging",
            "observation": "exception",
            "ingestion_id": str(UUID(int=1)),
            "execution_key": str(UUID(int=2)),
            "batch_number": 0,
            "attempt_id": 11,
            "pipeline_run_id": 21,
            "code": "staging_integrity_error",
            "category": "integrity",
            "reason": "unspecified",
            "message": "The staging data failed an integrity check.",
            "recovery": "operator_review",
        }
        assert json.loads(payload) == expected
        (record,) = records
        assert record.etl == expected
        assert record.exc_info is None and record.stack_info is None and record.args == ()
        assert "Traceback" not in rendered and "untrusted_metadata" not in rendered
        assert all(token not in rendered for token in SECRET.split())
    finally:
        logger.removeHandler(handler)
        handler.close()
        stream.close()


@pytest.mark.parametrize("has_primary_error", [False, True])
def test_serialization_failure_cannot_change_processing_outcome(
    monkeypatch, caplog, has_primary_error
):
    original = AttemptConflictError(SECRET)
    serializer = Mock(side_effect=RuntimeError("synthetic serialization failure"))
    monkeypatch.setattr(diagnostics.json, "dumps", serializer)

    def operation():
        with CoordinatorDiagnostic(pipeline.logger, "run_staged_batch", UUID(int=1)) as diagnostic:
            if has_primary_error:
                raise original
            diagnostic.failed_result()
        return "accepted result"

    if has_primary_error:
        with pytest.raises(AttemptConflictError) as caught:
            operation()
        assert caught.value is original
    else:
        assert operation() == "accepted result"
    serializer.assert_called_once()
    assert events(caplog) == []
