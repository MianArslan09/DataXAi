"""Gate 1 integration on the seeded local upstream and the serial disposable application DB."""

import json
import logging
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from decouple import config
from django.db import connections
from django.test.utils import CaptureQueriesContext
from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from etl.error_handling import classify_etl_failure
from etl.exceptions import ETLError
from etl.ingestion_runs import IngestionExecutionSummary, resume_staged_range, run_ingestion
from etl.models import (
    IngestionCompletionProof,
    PipelineRun,
    StagingBatch,
    StagingIngestion,
    StagingRecord,
    TransformationAttempt,
    TransformationRecordResult,
)
from etl.serialization import encode_source_batch
from etl.sources import CANONICAL_COLUMNS, PostgresSource, postgres_source
from etl.sources._cleanup import cleanup_on_exit
from etl.staging_reader import load_source_batch
from etl.tests.test_coordinator_failures_postgres import (
    assert_exact_execution,
    database,
    execution_counts,
)
from etl.tests.test_sources_postgres import (
    _probe,
    expected_retail_rows,
    required_source_url,
    retail_row,
)
from etl.tests.test_staging_constraints import staging_postgres as disposable_postgres  # noqa: F401
from etl.tests.test_staging_persistence import application_postgres as application_postgres
from etl.transforms.date_contract import SourceDateProfile


@pytest.fixture(scope="module")
def source_url():
    url = required_source_url()
    _probe(url, required=True)
    return make_url(url)  # Mask the password in pytest fixture representations.


@pytest.fixture(scope="module")
def staging_postgres(source_url, request):
    """Validate both targets before the existing fixture can CREATE DATABASE."""
    if (
        config("POSTGRES_HOST", default="127.0.0.1") not in {"localhost", "127.0.0.1", "::1"}
        or config("POSTGRES_PORT", default="5432") != "5432"
        or any(os.environ.get(key) for key in ("PGHOSTADDR", "PGSERVICE", "PGOPTIONS"))
    ):
        pytest.fail("Expected the separate local application test server.", pytrace=False)
    try:
        with psycopg.connect(
            dbname="postgres",
            host=config("POSTGRES_HOST", default="127.0.0.1"),
            port=5432,
            user=config("POSTGRES_USER", default="dataxai"),
            password=config("POSTGRES_PASSWORD", default="dataxai"),
            connect_timeout=3,
        ) as admin:
            row = admin.execute(
                "SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname = current_user"
            ).fetchone()
    except psycopg.Error:
        row = None
    if row != (True,):
        pytest.fail("Application fixture requires local login and CREATE DATABASE.", pytrace=False)
    return request.getfixturevalue("disposable_postgres")


@pytest.fixture
def options():
    database()  # Checks the active default is PostgreSQL and its UUID disposable name.
    ingestion = StagingIngestion.objects.create(source_kind="POSTGRES", source_reference="retail")
    return dict(
        ingestion_id=ingestion.pk,
        execution_key=uuid4(),
        profile=SourceDateProfile(),
        transformation_version="source-integration-v1",
        claim_ttl_seconds=60,
    )


def assert_open(options):
    ingestion = StagingIngestion.objects.get(pk=options["ingestion_id"])
    assert ingestion.status == "OPEN"
    assert ingestion.final_batch_count is ingestion.final_row_count is None
    assert ingestion.completed_at is ingestion.aborted_at is None
    assert not IngestionCompletionProof.objects.filter(pk=ingestion.pk).exists()


def wire(batch):
    # Snapshot immediately, before yielding the mutable DataFrame to the coordinator.
    return json.dumps(encode_source_batch(batch), sort_keys=True, allow_nan=False).encode("ascii")


def execution_state():
    # Tiny serial database: include all columns, all execution keys and unlinked runs.
    return tuple(
        list(model.objects.order_by("pk").values())
        for model in (
            StagingIngestion,
            StagingBatch,
            StagingRecord,
            TransformationAttempt,
            PipelineRun,
            TransformationRecordResult,
            IngestionCompletionProof,
        )
    )


def assert_accounting(options, sizes, before):
    assert_exact_execution(
        options["ingestion_id"], options["execution_key"], [(size, 0) for size in sizes], before
    )
    rows = TransformationRecordResult.objects.filter(batch__ingestion_id=options["ingestion_id"])
    assert not rows.exclude(
        interpretation_evidence={
            "schema": "date-outcome-v1",
            "timezone_status": "TIMEZONE_UNKNOWN",
            "timezone_origin": "unspecified",
            "issue_code": None,
        }
    ).exists()
    assert_open(options)


def expected_summary(options, sizes, *, live):
    return IngestionExecutionSummary(
        ingestion_id=options["ingestion_id"],
        execution_key=options["execution_key"],
        operation="run_ingestion" if live else "resume_staged_range",
        requested_first_batch=None if live else 0,
        requested_last_batch=None if live else len(sizes) - 1,
        successful_batches=len(sizes),
        successful_rows=sum(sizes),
        safe_rows=sum(sizes),
        unresolved_rows=0,
        source_exhausted=live,
        failed_batch_number=None,
        failed_run_id=None,
    )


def assert_staged_replay(options, sizes, before, monkeypatch):
    prior = execution_state()

    def forbidden(*args, **kwargs):
        pytest.fail("Staged replay attempted upstream access.")

    monkeypatch.setattr(PostgresSource, "__enter__", forbidden)
    monkeypatch.setattr(postgres_source, "create_engine", forbidden)
    with CaptureQueriesContext(database()) as queries:
        replay = resume_staged_range(**options, first_batch=0, last_batch=len(sizes) - 1)
    assert replay == expected_summary(options, sizes, live=False)
    assert execution_state() == prior
    assert not any(
        q["sql"].lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"} for q in queries
    )
    assert_accounting(options, sizes, before)


def test_postgres_live_ingestion_and_staged_replay(source_url, options, monkeypatch):
    sizes = [5, 5, 5, 3]
    before = execution_counts()
    source = PostgresSource(source_url, batch_size=5)
    extract = source.extract
    original_batches = []

    def observe():
        for batch in extract():
            original_batches.append(wire(batch))
            yield batch

    monkeypatch.setattr(source, "extract", observe)
    result = run_ingestion(**options, source=source)
    assert result == expected_summary(options, sizes, live=True)
    assert_accounting(options, sizes, before)
    contents = Counter()
    for number, (original, size) in enumerate(zip(original_batches, sizes, strict=True)):
        restored = load_source_batch(ingestion_id=options["ingestion_id"], batch_number=number)
        assert restored.source_name == "postgres:raw_transactions"
        assert restored.batch_number == number
        assert tuple(restored.data.columns) == CANONICAL_COLUMNS and len(restored) == size
        assert wire(restored) == original
        saved = StagingBatch.objects.get(ingestion_id=options["ingestion_id"], batch_number=number)
        captured = json.loads(original)
        records = captured.pop("records")
        assert saved.header == captured
        saved_rows = saved.records.order_by("row_position").values_list(
            "row_position", "record_payload"
        )
        assert list(saved_rows) == list(enumerate(records))
        contents.update(retail_row(row) for row in restored.data.itertuples(index=False, name=None))
    # Full rows include signed quantity/price; Counter retains each duplicate occurrence.
    assert contents == expected_retail_rows()
    assert_staged_replay(options, sizes, before, monkeypatch)


@pytest.fixture
def driver_events(monkeypatch):
    """Observe SQLAlchemy's actual engine, driver exceptions and disposal."""
    create = postgres_source.create_engine
    errors, opened, disposed = [], [], []

    def observe(*args, **kwargs):
        engine = create(*args, **kwargs)
        event.listen(engine, "engine_connect", lambda conn: opened.append(conn))
        event.listen(engine, "engine_disposed", disposed.append)

        @event.listens_for(engine, "handle_error")
        def observed_error(context):
            errors.append((context.original_exception, context.sqlalchemy_exception))

        return engine

    monkeypatch.setattr(postgres_source, "create_engine", observe)
    return errors, opened, disposed


def assert_source_failure(error, source, driver_events, options, caplog):
    errors, opened, disposed = driver_events
    wrapped = error.__cause__
    assert isinstance(wrapped, DBAPIError) and isinstance(wrapped.orig, psycopg.Error)
    assert any(driver is wrapped.orig and wrapper is wrapped for driver, wrapper in errors)
    (conn,) = opened
    assert conn.closed and disposed == [conn.engine]
    assert source._connection is source._engine is None

    (record,) = [r for r in caplog.records if r.getMessage().startswith("etl.coordinator_failure ")]
    failure = classify_etl_failure(error)
    expected = {
        **failure.as_dict(),
        "operation": "run_ingestion",
        "stage": "source_read",
        "observation": "exception",
        "ingestion_id": str(options["ingestion_id"]),
        "execution_key": str(options["execution_key"]),
    }
    assert record.exc_info is record.stack_info is None and record.args == ()
    rendered = logging.Formatter("%(message)s").format(record)
    assert rendered == "etl.coordinator_failure " + json.dumps(expected, sort_keys=True)
    assert record.etl == expected and source.table not in rendered
    assert failure.code == error.code
    assert failure.message == classify_etl_failure(wrapped.orig).message
    return wrapped.orig, failure


def test_postgres_query_failure_preserves_empty_ingestion(
    source_url, options, driver_events, caplog
):
    before = execution_state()
    counts = execution_counts()
    source = PostgresSource(source_url, table="missing_" + uuid4().hex, batch_size=5)
    with pytest.raises(ETLError) as caught:
        run_ingestion(**options, source=source)
    driver, failure = assert_source_failure(caught.value, source, driver_events, options, caplog)
    assert isinstance(driver, psycopg.errors.UndefinedTable) and driver.sqlstate == "42P01"
    assert failure.message == "A database authentication, permission or schema check failed."
    assert type(caught.value) is ETLError
    assert (failure.category, failure.reason, failure.recovery) == (
        "configuration",
        "database_configuration",
        "operator_review",
    )
    assert execution_state() == before
    assert_accounting(options, [], counts)


def assert_committed_prefix(options, counts, writer_pid):
    # Django gives this worker its own backend. Observe committed rows before disconnecting.
    db = connections["default"]
    try:
        with db.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid(), current_database()")
            pid, name = cursor.fetchone()
        assert pid != writer_pid and name == database().settings_dict["NAME"]
        assert_accounting(options, [5], counts)
        return execution_state()
    finally:
        db.close()


def test_postgres_fetch_disconnect_preserves_committed_prefix(
    source_url, options, driver_events, caplog, monkeypatch, record_property
):
    counts = execution_counts()
    source = PostgresSource(source_url, batch_size=5)
    with database().cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        (writer_pid,) = cursor.fetchone()
    read_sql = postgres_source.pd.read_sql
    driver_failures = []
    committed = None

    def interrupt(query, conn, **kwargs):
        nonlocal committed
        chunks = read_sql(query, conn, **kwargs)
        with cleanup_on_exit(chunks.close):
            yield next(chunks)
            with ThreadPoolExecutor(max_workers=1) as pool:
                observer = pool.submit(assert_committed_prefix, options, counts, writer_pid)
                committed = observer.result(timeout=20)
            driver = conn.connection.driver_connection
            driver.close()
            assert driver.closed
            try:
                buffered = next(chunks)
            except StopIteration:
                pytest.fail("The 18-row fixture ended after the first five rows.")
            except DBAPIError as exc:
                record_property("disconnect_failure_operation", "next_fetch")
                driver_failures.append(exc)
                raise

            # Keep this buffered chunk out of staging while probing the closed connection.
            assert len(buffered) == 5
            try:
                conn.exec_driver_sql("SELECT 1")
            except DBAPIError as exc:
                record_property("disconnect_failure_operation", "connection_probe")
                driver_failures.append(exc)
                raise
            pytest.fail("Closed source connection unexpectedly completed a driver round trip.")

    monkeypatch.setattr(postgres_source.pd, "read_sql", interrupt)
    with pytest.raises(ETLError) as caught:
        run_ingestion(**options, source=source)
    assert committed is not None and execution_state() == committed
    assert len(driver_failures) == 1 and driver_failures[0] is caught.value.__cause__
    driver, failure = assert_source_failure(caught.value, source, driver_events, options, caplog)
    assert failure.category == "database"
    expected = classify_etl_failure(driver)
    assert (failure.reason, failure.recovery) == (expected.reason, expected.recovery)
    if driver.sqlstate is None:
        assert (failure.reason, failure.recovery) == ("unspecified", "operator_review")
    assert_accounting(options, [5], counts)
    assert_staged_replay(options, [5], counts, monkeypatch)
