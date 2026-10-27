"""Real PostgreSQL schema checks in a newly created, uniquely named test database.

No application/source tables are migrated or modified. Set
DATAXAI_REQUIRE_STAGING_POSTGRES=1 to turn unavailable PostgreSQL/CREATE DATABASE
permission into a failure. Only this fixture's successfully created database is
dropped; cleanup never uses FORCE and never targets an existing database.
"""

import json
import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pandas as pd
import psycopg
import pytest
from decouple import config
from django.core.exceptions import ValidationError
from django.db import DataError, IntegrityError, connections, transaction
from django.db.migrations.executor import MigrationExecutor
from pandas.testing import assert_frame_equal
from psycopg import sql

from etl.models import (
    PipelineRun,
    StagingBatch,
    StagingIngestion,
    StagingRecord,
    TransformationAttempt,
    TransformationRecordResult,
)
from etl.serialization import decode_source_batch, encode_source_batch, fingerprint_source_batch
from etl.sources.contract import CANONICAL_COLUMNS, SourceBatch


def _postgres_unavailable():
    reason = (
        "Staging PostgreSQL verification requires reachable POSTGRES_* application-server "
        "configuration and permission to create a disposable test database; credentials withheld."
    )
    if os.environ.get("DATAXAI_REQUIRE_STAGING_POSTGRES") == "1":
        pytest.fail(reason, pytrace=False)
    pytest.skip(reason)


@pytest.fixture(scope="module")
def staging_postgres(django_db_blocker):
    database_name = "test_dataxai_staging_" + uuid4().hex
    alias = "staging_" + uuid4().hex
    parameters = {
        "host": config("POSTGRES_HOST", default="127.0.0.1"),
        "port": config("POSTGRES_PORT", default="5432"),
        "user": config("POSTGRES_USER", default="dataxai"),
        "password": config("POSTGRES_PASSWORD", default="dataxai"),
        "connect_timeout": 3,
    }
    try:
        maintenance = psycopg.connect(dbname="postgres", autocommit=True, **parameters)
    except psycopg.OperationalError:
        maintenance = None
    if maintenance is None:
        _postgres_unavailable()
    created = False
    registered = False
    try:
        try:
            maintenance.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        except psycopg.errors.InsufficientPrivilege:
            denied = True
        else:
            denied = False
        if denied:
            _postgres_unavailable()
        created = True
        database = deepcopy(connections["default"].settings_dict)
        database.update(
            ENGINE="django.db.backends.postgresql",
            NAME=database_name,
            USER=parameters["user"],
            PASSWORD=parameters["password"],
            HOST=parameters["host"],
            PORT=parameters["port"],
            OPTIONS={"connect_timeout": 3},
            CONN_MAX_AGE=0,
        )
        connections.databases[alias] = database
        registered = True
        with django_db_blocker.unblock():
            executor = MigrationExecutor(connections[alias])
            executor.migrate(executor.loader.graph.leaf_nodes())
            yield alias
    finally:
        with django_db_blocker.unblock():
            if registered:
                connections[alias].close()
                del connections[alias]
                del connections.databases[alias]
        if created:
            # The exact identifier was generated here, and CREATE succeeded above.
            maintenance.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name)))
        maintenance.close()


@pytest.fixture
def graph(staging_postgres):
    """Two independent batches provide unambiguous cross-batch counterexamples."""
    alias = staging_postgres
    ingestion = StagingIngestion.objects.using(alias).create(
        source_kind="CSV", source_reference="synthetic-import"
    )
    batches, records, attempts = [], [], []
    for number in range(2):
        batch = StagingBatch.objects.using(alias).create(
            ingestion=ingestion,
            batch_number=number,
            source_name="synthetic-source",
            header={"synthetic": True},
            row_count=1,
            content_hash="a" * 64,
        )
        batches.append(batch)
        records.append(
            StagingRecord.objects.using(alias).create(
                batch=batch, row_position=0, record_payload={"synthetic": True}
            )
        )
        attempts.append(
            TransformationAttempt.objects.using(alias).create(
                batch=batch, configuration_hash="b" * 64, transformation_version="synthetic-v1"
            )
        )
    return alias, ingestion, batches, records, attempts


def _result(graph, **overrides):
    alias, _, batches, records, attempts = graph
    values = {
        "batch": batches[0],
        "attempt": attempts[0],
        "staged_record": records[0],
        "outcome": "SAFE",
    }
    values.update(overrides)
    return TransformationRecordResult.objects.using(alias).create(**values)


@pytest.mark.parametrize("kind", ["batch", "record", "attempt", "result"])
def test_unique_identity_pairs_are_database_enforced(graph, kind):
    alias, ingestion, batches, records, attempts = graph
    if kind == "result":
        _result(graph)
    with pytest.raises(IntegrityError), transaction.atomic(using=alias):
        if kind == "batch":
            StagingBatch.objects.using(alias).create(
                ingestion=ingestion,
                batch_number=0,
                source_name="duplicate",
                header={},
                row_count=0,
                content_hash="c" * 64,
            )
        elif kind == "record":
            StagingRecord.objects.using(alias).create(
                batch=batches[0], row_position=records[0].row_position, record_payload={}
            )
        elif kind == "attempt":
            TransformationAttempt.objects.using(alias).create(
                batch=batches[0],
                attempt_key=attempts[0].attempt_key,
                configuration_hash="c" * 64,
                transformation_version="v2",
            )
        else:
            _result(graph, outcome="UNRESOLVED")


@pytest.mark.parametrize("relationship", ["attempt", "staged_record", "batch"])
@pytest.mark.parametrize("operation", ["insert", "update"])
def test_composite_membership_rejects_cross_batch_results(graph, relationship, operation):
    alias, _, batches, records, attempts = graph
    foreign = {"batch": batches[1], "attempt": attempts[1], "staged_record": records[1]}
    instance = _result(graph) if operation == "update" else None
    with pytest.raises(IntegrityError), transaction.atomic(using=alias):
        if instance is None:
            _result(graph, **{relationship: foreign[relationship]})
        else:
            TransformationRecordResult.objects.using(alias).filter(pk=instance.pk).update(
                **{relationship: foreign[relationship]}
            )


@pytest.mark.parametrize("parent", ["attempt", "record"])
def test_composite_membership_prevents_reparenting_referenced_rows(graph, parent):
    alias, _, batches, records, attempts = graph
    _result(graph)
    model, instance = (
        (TransformationAttempt, attempts[0]) if parent == "attempt" else (StagingRecord, records[0])
    )
    # Avoid a separate position-uniqueness failure masking the composite FK check.
    changes = {"batch": batches[1]}
    if parent == "record":
        changes["row_position"] = 10
    with pytest.raises(IntegrityError) as caught, transaction.atomic(using=alias):
        model.objects.using(alias).filter(pk=instance.pk).update(**changes)
    expected = (
        "stg_result_attempt_batch_fk" if parent == "attempt" else "stg_result_record_batch_fk"
    )
    assert caught.value.__cause__.diag.constraint_name == expected


@pytest.mark.parametrize(
    ("target", "changes"),
    [
        ("ingestion", {"source_kind": "OTHER"}),
        ("ingestion", {"source_reference": "postgresql://synthetic.invalid/source"}),
        ("ingestion", {"source_reference": "source\n"}),
        ("ingestion", {"status": "OTHER"}),
        ("ingestion", {"status": "COMPLETE"}),
        ("ingestion", {"status": "ABORTED"}),
        ("ingestion", {"final_row_count": 1}),
        ("ingestion", {"final_batch_count": -1}),
        ("ingestion", {"final_row_count": -1}),
        ("batch", {"batch_number": -1}),
        ("batch", {"row_count": -1}),
        ("batch", {"serializer_version": 2}),
        ("batch", {"content_hash": "A" * 64}),
        ("batch", {"content_hash": "a" * 63}),
        ("batch", {"content_hash": "a" * 63 + "\n"}),
        ("record", {"row_position": -1}),
        ("attempt", {"status": "OTHER"}),
        ("attempt", {"status": "RUNNING"}),
        ("attempt", {"status": "SUCCEEDED"}),
        ("attempt", {"status": "FAILED"}),
        ("attempt", {"configuration_hash": "g" * 64}),
        ("attempt", {"configuration_hash": "b" * 63}),
        ("attempt", {"claim_generation": -1}),
        ("attempt", {"claim_owner": "synthetic-worker"}),
        ("attempt", {"claim_expires_at": datetime(2026, 10, 25, tzinfo=timezone.utc)}),
        (
            "attempt",
            {
                "claim_owner": "synthetic-worker",
                "claim_expires_at": datetime(2026, 10, 25, tzinfo=timezone.utc),
                "claim_generation": 0,
            },
        ),
        (
            "attempt",
            {
                "claim_owner": "",
                "claim_expires_at": datetime(2026, 10, 25, tzinfo=timezone.utc),
                "claim_generation": 1,
            },
        ),
        ("attempt", {"safe_row_count": -1}),
        ("attempt", {"unresolved_row_count": -1}),
        ("attempt", {"failure_code": "FAILURE"}),
        ("result", {"outcome": "OTHER"}),
    ],
)
def test_database_checks_reject_invalid_raw_updates(graph, target, changes):
    alias, ingestion, batches, records, attempts = graph
    instance = {
        "ingestion": ingestion,
        "batch": batches[0],
        "record": records[0],
        "attempt": attempts[0],
        "result": _result(graph),
    }[target]
    query = type(instance).objects.using(alias).filter(pk=instance.pk)
    now = datetime.now(timezone.utc)
    if target == "ingestion" and any(value == -1 for value in changes.values()):
        query.update(status="COMPLETE", completed_at=now, final_batch_count=0, final_row_count=0)
    if target == "attempt" and changes.keys() & {"safe_row_count", "unresolved_row_count"}:
        query.update(
            status="SUCCEEDED",
            started_at=now,
            finished_at=now,
            safe_row_count=0,
            unresolved_row_count=0,
        )
    with pytest.raises(IntegrityError), transaction.atomic(using=alias):
        query.update(**changes)


def test_database_accepts_valid_lifecycle_and_claim_states(graph):
    alias, ingestion, batches, records, attempts = graph
    now = datetime.now(timezone.utc)
    StagingIngestion.objects.using(alias).filter(pk=ingestion.pk).update(
        status="COMPLETE", completed_at=now, final_batch_count=2, final_row_count=2
    )
    StagingIngestion.objects.using(alias).create(
        source_kind="POSTGRES", source_reference="aborted-import", status="ABORTED", aborted_at=now
    )
    query = TransformationAttempt.objects.using(alias).filter(pk=attempts[0].pk)
    query.update(
        status="RUNNING",
        started_at=now,
        claim_owner="synthetic-worker",
        claim_generation=1,
        claim_expires_at=now + timedelta(minutes=5),
    )
    query.update(status="SUCCEEDED", finished_at=now, safe_row_count=1, unresolved_row_count=0)
    attempts[0].refresh_from_db(using=alias)
    assert attempts[0].status == "SUCCEEDED"
    assert attempts[0].safe_row_count == 1
    TransformationAttempt.objects.using(alias).filter(pk=attempts[1].pk).update(
        status="FAILED",
        finished_at=now,
        failure_code="SYNTHETIC",
        failure_diagnostics={"safe": True},
    )
    attempts[1].refresh_from_db(using=alias)
    assert attempts[1].started_at is None
    assert attempts[1].failure_diagnostics == {"safe": True}
    query.update(claim_owner=None, claim_expires_at=None)
    attempts[0].refresh_from_db(using=alias)
    assert attempts[0].claim_owner is attempts[0].claim_expires_at is None
    assert attempts[0].claim_generation == 1
    assert _result(graph).outcome == "SAFE"
    assert (
        _result(
            graph,
            batch=batches[1],
            attempt=attempts[1],
            staged_record=records[1],
            outcome="UNRESOLVED",
        ).outcome
        == "UNRESOLVED"
    )


@pytest.mark.parametrize(
    ("field", "limit"), [("configuration", 16384), ("failure_diagnostics", 4096)]
)
def test_json_character_bounds_include_exact_boundary(graph, field, limit):
    alias, _, _, _, attempts = graph
    query = TransformationAttempt.objects.using(alias).filter(pk=attempts[0].pk)
    if field == "failure_diagnostics":
        query.update(status="FAILED", finished_at=datetime.now(timezone.utc))
    # PostgreSQL's jsonb text is {"x": "..."}, an overhead of nine characters.
    query.update(**{field: {"x": "x" * (limit - 9)}})
    with connections[alias].cursor() as cursor:
        cursor.execute(
            f'SELECT length("{field}"::text) FROM etl_transformationattempt WHERE id = %s',
            [attempts[0].pk],
        )
        assert cursor.fetchone()[0] == limit
    with pytest.raises(IntegrityError), transaction.atomic(using=alias):
        query.update(**{field: {"x": "x" * (limit - 8)}})


def test_composite_constraints_exist_with_exact_columns_and_immediate_checks(staging_postgres):
    with connections[staging_postgres].cursor() as cursor:
        cursor.execute(
            "SELECT conname, pg_get_constraintdef(oid), condeferrable, condeferred "
            "FROM pg_constraint WHERE conname IN (%s, %s, %s, %s)",
            [
                "stg_result_attempt_batch_fk",
                "stg_result_record_batch_fk",
                "stg_attempt_id_batch_unique",
                "stg_record_id_batch_unique",
            ],
        )
        constraints = {
            name: (definition, deferrable, deferred)
            for name, definition, deferrable, deferred in cursor.fetchall()
        }
    assert len(constraints) == 4
    assert constraints["stg_result_attempt_batch_fk"] == (
        "FOREIGN KEY (attempt_id, batch_id) REFERENCES etl_transformationattempt(id, batch_id)",
        False,
        False,
    )
    assert constraints["stg_result_record_batch_fk"] == (
        "FOREIGN KEY (staged_record_id, batch_id) REFERENCES etl_stagingrecord(id, batch_id)",
        False,
        False,
    )
    for name in ("stg_attempt_id_batch_unique", "stg_record_id_batch_unique"):
        assert constraints[name] == ("UNIQUE (id, batch_id)", False, False)


@pytest.mark.parametrize("suffix", ["plain", "nul\x00literal\\u0000"])
def test_ser_v1_encoded_records_roundtrip_through_postgres_text(staging_postgres, suffix):
    alias = staging_postgres
    row = [
        "0001",
        "POST",
        "synthetic \u0627\u0631\u062f\u0648 " + suffix,
        -1,
        pd.Timestamp("2026-10-20T14:30:00.123456789"),
        Decimal("-0.0100"),
        None,
        "Pakistan",
        "extra " + suffix,
    ]
    frame = pd.DataFrame(
        [row, row.copy()],
        columns=[*CANONICAL_COLUMNS, "extra " + suffix],
        index=["same " + suffix, "same " + suffix],
        dtype=object,
    )
    frame.columns.name = "columns " + suffix
    frame.index.name = "index " + suffix
    source = SourceBatch(
        frame,
        "synthetic-postgres-text " + suffix,
        0,
        datetime(2026, 10, 20, tzinfo=timezone.utc),
    )
    payload = encode_source_batch(source)
    ingestion = StagingIngestion.objects.using(alias).create(
        source_kind="CSV", source_reference="synthetic-text"
    )
    batch = StagingBatch.objects.using(alias).create(
        ingestion=ingestion,
        batch_number=0,
        source_name=source.source_name,
        header={key: value for key, value in payload.items() if key != "records"},
        row_count=2,
        content_hash=fingerprint_source_batch(source),
    )
    for record in payload["records"]:
        StagingRecord.objects.using(alias).create(
            batch=batch, row_position=record["row_position"], record_payload=record
        )
    batch.refresh_from_db(using=alias)
    assert batch.source_name == source.source_name
    with connections[alias].cursor() as cursor:
        cursor.execute(
            "SELECT source_name, header, pg_typeof(source_name)::text, pg_typeof(header)::text "
            "FROM etl_stagingbatch WHERE id = %s",
            [batch.pk],
        )
        stored_name, stored_header, name_type, header_type = cursor.fetchone()
        assert name_type == header_type == "text"
        assert json.loads(stored_name) == source.source_name
        assert json.loads(stored_header) == batch.header
        assert stored_name.isascii() and stored_header.isascii()
        assert "\x00" not in stored_name + stored_header
        cursor.execute(
            "SELECT record_payload, pg_typeof(record_payload)::text "
            "FROM etl_stagingrecord WHERE batch_id = %s ORDER BY row_position",
            [batch.pk],
        )
        for position, (stored_record, record_type) in enumerate(cursor.fetchall()):
            assert record_type == "text"
            assert stored_record.isascii() and "\x00" not in stored_record
            assert json.loads(stored_record) == payload["records"][position]
    records = list(
        StagingRecord.objects.using(alias)
        .filter(batch=batch)
        .order_by("row_position")
        .values_list("record_payload", flat=True)
    )
    reconstructed_payload = {**batch.header, "records": records}
    assert reconstructed_payload == payload
    reconstructed = decode_source_batch(reconstructed_payload)
    assert_frame_equal(source.data, reconstructed.data, check_exact=True)
    assert reconstructed.data.iloc[0, 5].as_tuple() == Decimal("-0.0100").as_tuple()
    assert fingerprint_source_batch(reconstructed) == batch.content_hash
    assert len(records) == batch.row_count == 2
    assert reconstructed.extracted_at == source.extracted_at
    assert reconstructed.source_name == source.source_name
    assert reconstructed.batch_number == source.batch_number
    assert encode_source_batch(source) == payload


@pytest.mark.parametrize(
    ("field", "stored", "error"),
    [
        ("header", '{"broken":', DataError),
        ("record_payload", "[", DataError),
        ("source_name", '"unterminated', DataError),
        ("header", "[]", IntegrityError),
        ("record_payload", "null", IntegrityError),
        ("source_name", "{}", IntegrityError),
    ],
)
def test_database_rejects_invalid_stored_json_text(graph, field, stored, error):
    alias, _, batches, records, _ = graph
    instance = records[0] if field == "record_payload" else batches[0]
    with pytest.raises(error), transaction.atomic(using=alias):
        with connections[alias].cursor() as cursor:
            cursor.execute(
                sql.SQL("UPDATE {} SET {} = %s WHERE id = %s").format(
                    sql.Identifier(instance._meta.db_table), sql.Identifier(field)
                ),
                [stored, instance.pk],
            )


def test_read_rejects_duplicate_json_keys_in_manually_written_text(graph):
    alias, _, _, records, _ = graph
    with connections[alias].cursor() as cursor:
        cursor.execute(
            "UPDATE etl_stagingrecord SET record_payload = %s WHERE id = %s",
            ['{"values":[],"values":[1]}', records[0].pk],
        )
    with pytest.raises(ValidationError, match="Invalid stored staging JSON"):
        records[0].refresh_from_db(using=alias)


def test_migration_reverses_and_reapplies_only_in_disposable_database(staging_postgres):
    alias = staging_postgres
    connection = connections[alias]
    assert connection.settings_dict["NAME"].startswith("test_dataxai_staging_")
    run = PipelineRun.objects.using(alias).create(notes="disposable database sentinel")
    MigrationExecutor(connection).migrate([("etl", "0001_initial")])
    tables = set(connection.introspection.table_names())
    assert "etl_pipelinerun" in tables
    assert all(
        model._meta.db_table not in tables
        for model in (
            StagingIngestion,
            StagingBatch,
            StagingRecord,
            TransformationAttempt,
            TransformationRecordResult,
        )
    )
    assert PipelineRun.objects.using(alias).get(pk=run.pk).notes == "disposable database sentinel"
    MigrationExecutor(connection).migrate([("etl", "0002_staging_schema")])
    assert "etl_transformationrecordresult" in connection.introspection.table_names()
    test_composite_constraints_exist_with_exact_columns_and_immediate_checks(alias)
    assert PipelineRun.objects.using(alias).get(pk=run.pk).status == PipelineRun.Status.PENDING
