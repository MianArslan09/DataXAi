"""Deterministic source failures; no external database or source service."""

from types import SimpleNamespace
from unittest.mock import Mock
from xml.etree.ElementTree import ParseError

import pandas as pd
import psycopg
import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

from etl.exceptions import ETLError, MalformedSourceError, SourceConnectionError
from etl.sources import csv_source, excel_source, postgres_source
from etl.sources.contract import CANONICAL_COLUMNS

SECRET = "password=gate2-secret SELECT private_value FROM private_table"


def frame():
    return pd.DataFrame([[1] * len(CANONICAL_COLUMNS)], columns=CANONICAL_COLUMNS)


class Rows:
    def __init__(self, values=(), failure=None, cleanup=None):
        self.values = iter(values)
        self.failure = failure
        self.close = Mock(side_effect=cleanup)

    def __iter__(self):
        return self

    def __next__(self):
        try:
            return next(self.values)
        except StopIteration:
            if self.failure is not None:
                raise self.failure from None
            raise


@pytest.mark.parametrize("error", [pd.errors.ParserError, pd.errors.EmptyDataError])
def test_csv_open_parser_failure_is_safe(monkeypatch, error):
    original = error(SECRET)
    monkeypatch.setattr(csv_source.pd, "read_csv", Mock(side_effect=original))
    with pytest.raises(MalformedSourceError) as caught:
        csv_source.CsvSource("orders.csv")._open("unused")
    assert caught.value.__cause__ is original
    assert SECRET not in str(caught.value)


def test_csv_decoding_failure_is_not_malformed_input():
    original = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte")
    source = csv_source.CsvSource("orders.csv")
    source._reader = Rows(failure=original)
    with pytest.raises(UnicodeDecodeError) as caught:
        list(source.extract())
    assert caught.value is original


@pytest.mark.parametrize("kind", ["csv", "excel"])
def test_source_close_preserves_primary_and_clears_handle(kind):
    source = (
        csv_source.CsvSource("orders.csv")
        if kind == "csv"
        else excel_source.ExcelSource("orders.xlsx")
    )
    attribute = "_reader" if kind == "csv" else "_workbook"
    resource = SimpleNamespace(close=Mock(side_effect=OSError("cleanup failed")))
    setattr(source, attribute, resource)
    primary = KeyboardInterrupt("interrupted")
    source.__exit__(type(primary), primary, None)
    assert getattr(source, attribute) is None
    resource.close.assert_called_once()


def test_excel_open_preserves_infrastructure_error(monkeypatch):
    original = PermissionError(SECRET)
    monkeypatch.setattr(excel_source.openpyxl, "load_workbook", Mock(side_effect=original))
    with pytest.raises(PermissionError) as caught:
        excel_source.ExcelSource("orders.xlsx")._open("unused")
    assert caught.value is original


def test_excel_iteration_parser_error_is_not_successful_exhaustion():
    rows = Rows([CANONICAL_COLUMNS, [1] * 8], failure=ParseError(SECRET))
    worksheet = SimpleNamespace(iter_rows=lambda **kwargs: rows)
    source = excel_source.ExcelSource("orders.xlsx", batch_size=1)
    source._workbook = {"Sheet": worksheet}
    source.sheet_names = ["Sheet"]
    iterator = source.extract()
    assert next(iterator).batch_number == 0
    with pytest.raises(MalformedSourceError):
        next(iterator)
    rows.close.assert_called_once()


def fake_postgres(monkeypatch):
    connection = Mock()
    connection.execution_options.return_value = connection
    engine = Mock()
    engine.connect.return_value = connection
    monkeypatch.setattr(postgres_source, "create_engine", Mock(return_value=engine))
    return postgres_source.PostgresSource("postgresql+psycopg://unused"), engine, connection


def test_postgres_query_failure_is_safe_and_not_connection_failure(monkeypatch):
    source, engine, connection = fake_postgres(monkeypatch)
    original = ProgrammingError(SECRET, {"password": SECRET}, psycopg.errors.UndefinedTable(SECRET))
    monkeypatch.setattr(postgres_source.pd, "read_sql", Mock(side_effect=original))
    with pytest.raises(ETLError) as caught, source:
        list(source.extract())
    assert type(caught.value) is ETLError
    assert caught.value.__cause__ is original
    assert SECRET not in str(caught.value)
    engine.dispose.assert_called_once()
    connection.close.assert_called_once()


def test_postgres_open_failure_survives_both_cleanup_failures(monkeypatch):
    source, engine, connection = fake_postgres(monkeypatch)
    original = OperationalError(SECRET, None, psycopg.errors.ConnectionFailure(SECRET))
    connection.execute.side_effect = original
    connection.close.side_effect = OSError("close failure")
    engine.dispose.side_effect = OSError("dispose failure")
    with pytest.raises(SourceConnectionError) as caught:
        source.__enter__()
    assert caught.value.__cause__ is original
    assert SECRET not in str(caught.value)
    engine.dispose.assert_called_once()
    assert source._connection is source._engine is None


@pytest.mark.parametrize("phase", ["open", "iterate"])
@pytest.mark.parametrize("kind", ["io", "decode", "bug", "interrupt"])
def test_csv_preserves_non_parser_failures(monkeypatch, phase, kind):
    errors = {
        "io": PermissionError(SECRET),
        "decode": UnicodeDecodeError("utf-8", b"\xff", 0, 1, SECRET),
        "bug": RuntimeError(SECRET),
        "interrupt": KeyboardInterrupt(SECRET),
    }
    primary = errors[kind]
    source = csv_source.CsvSource("orders.csv")
    with pytest.raises(type(primary)) as caught:
        if phase == "open":
            monkeypatch.setattr(csv_source.pd, "read_csv", Mock(side_effect=primary))
            source._open("unused")
        else:
            source._reader = Rows([frame()], failure=primary)
            iterator = source.extract()
            assert next(iterator).batch_number == 0
            next(iterator)
    assert caught.value is primary


@pytest.mark.parametrize("kind", ["csv", "excel"])
def test_cleanup_only_failure_propagates_once_and_drops_adapter_reference(kind):
    source = (
        csv_source.CsvSource("orders.csv")
        if kind == "csv"
        else excel_source.ExcelSource("orders.xlsx")
    )
    attribute = "_reader" if kind == "csv" else "_workbook"
    primary = OSError("close failed")
    resource = SimpleNamespace(close=Mock(side_effect=primary))
    setattr(source, attribute, resource)
    with pytest.raises(OSError) as caught:
        source.__exit__(None, None, None)
    assert caught.value is primary
    assert getattr(source, attribute) is None
    source.__exit__(None, None, None)
    resource.close.assert_called_once()


@pytest.mark.parametrize("phase", ["open", "iterate"])
@pytest.mark.parametrize("kind", ["io", "bug", "value", "interrupt"])
def test_excel_preserves_unexpected_failures(monkeypatch, phase, kind):
    errors = {
        "io": OSError(SECRET),
        "bug": RuntimeError(SECRET),
        "value": ValueError(SECRET),
        "interrupt": KeyboardInterrupt(SECRET),
    }
    primary = errors[kind]
    source = excel_source.ExcelSource("orders.xlsx", batch_size=1)
    with pytest.raises(type(primary)) as caught:
        if phase == "open":
            monkeypatch.setattr(excel_source.openpyxl, "load_workbook", Mock(side_effect=primary))
            source._open("unused")
        else:
            rows = Rows([CANONICAL_COLUMNS, [1] * 8], failure=primary, cleanup=OSError("cleanup"))
            source._workbook = {"Sheet": SimpleNamespace(iter_rows=lambda **kwargs: rows)}
            source.sheet_names = ["Sheet"]
            iterator = source.extract()
            next(iterator)
            next(iterator)
    assert caught.value is primary
    if phase == "iterate":
        rows.close.assert_called_once()


def test_excel_open_failure_closes_owned_file(monkeypatch, tmp_path):
    path = tmp_path / "book.xlsx"
    path.write_bytes(b"placeholder")
    handles = []
    primary = RuntimeError("unexpected loader failure")

    def fail(stream, **kwargs):
        handles.append(stream)
        raise primary

    monkeypatch.setattr(excel_source.openpyxl, "load_workbook", fail)
    source = excel_source.ExcelSource(path)
    with pytest.raises(RuntimeError) as caught:
        source.__enter__()
    assert caught.value is primary
    assert len(handles) == 1 and handles[0].closed
    assert source._stream is source._workbook is None


def test_excel_unknown_sheet_preserves_primary_when_workbook_close_fails(monkeypatch):
    book = SimpleNamespace(sheetnames=["Present"], close=Mock(side_effect=OSError(SECRET)))
    monkeypatch.setattr(excel_source.openpyxl, "load_workbook", Mock(return_value=book))
    source = excel_source.ExcelSource("book.xlsx", sheet_names=["Missing"])
    with pytest.raises(MalformedSourceError):
        source._open("unused")
    book.close.assert_called_once()
    assert source._workbook is None


@pytest.mark.parametrize("suffix", [".xls", ".xlsb", ".csv"])
def test_excel_owned_handle_keeps_unsupported_extension_rejection(tmp_path, suffix):
    from .test_source_snapshots import FIXTURES

    path = tmp_path / ("book" + suffix)
    path.write_bytes((FIXTURES / "online_retail_ii_sample.xlsx").read_bytes())
    source = excel_source.ExcelSource(path)
    with pytest.raises(MalformedSourceError):
        source.__enter__()
    assert source._stream is source._workbook is None


@pytest.mark.parametrize("bound", [False, True])
def test_excel_close_failure_does_not_complete_sheet(bound):
    from etl.source_snapshots import _SheetVisits

    primary = OSError("row close failed")
    rows = Rows([CANONICAL_COLUMNS, [1] * 8], cleanup=primary)
    worksheet = SimpleNamespace(
        iter_rows=lambda **kwargs: rows, max_row=2, max_column=8, reset_dimensions=Mock()
    )
    source = excel_source.ExcelSource("book.xlsx", batch_size=1)
    source._workbook = {"Sheet": worksheet}
    source.sheet_names = ["Sheet"]
    visits = _SheetVisits() if bound else None
    iterator = source._extract(visits)
    assert next(iterator).source_name == "xlsx:book.xlsx:Sheet"
    with pytest.raises(OSError) as caught:
        next(iterator)
    assert caught.value is primary
    if bound:
        assert visits.entered == ["Sheet"]
        assert visits.completed == visits.counts == []
    rows.close.assert_called_once()


def test_explicit_excel_generator_close_exposes_cleanup_only_failure():
    primary = OSError("row close failed")
    rows = Rows([CANONICAL_COLUMNS, [1] * 8], cleanup=primary)
    source = excel_source.ExcelSource("book.xlsx", batch_size=1)
    source._workbook = {"Sheet": SimpleNamespace(iter_rows=lambda **kwargs: rows)}
    source.sheet_names = ["Sheet"]
    iterator = source.extract()
    next(iterator)
    with pytest.raises(OSError) as caught:
        iterator.close()
    assert caught.value is primary


@pytest.mark.parametrize("phase", ["connect", "query", "iterate"])
@pytest.mark.parametrize(
    "driver,error_type",
    [
        (psycopg.errors.ConnectionFailure, SourceConnectionError),
        (psycopg.errors.InvalidPassword, ETLError),
        (psycopg.errors.UndefinedTable, ETLError),
        (psycopg.errors.InsufficientPrivilege, ETLError),
        (psycopg.errors.SerializationFailure, ETLError),
    ],
)
def test_postgres_distinguishes_driver_reason_without_leaking(
    monkeypatch, phase, driver, error_type
):
    from core.exceptions import custom_exception_handler
    from etl.error_handling import classify_etl_failure

    source, engine, connection = fake_postgres(monkeypatch)
    original = OperationalError(SECRET, {"password": SECRET}, driver(SECRET))
    chunks = Rows([frame()], failure=original, cleanup=OSError("chunk cleanup"))
    if phase == "connect":
        engine.connect.side_effect = original
    elif phase == "query":
        monkeypatch.setattr(postgres_source.pd, "read_sql", Mock(side_effect=original))
    else:
        monkeypatch.setattr(postgres_source.pd, "read_sql", Mock(return_value=chunks))
    with pytest.raises(error_type) as caught, source:
        list(source.extract())
    assert type(caught.value) is error_type
    assert caught.value.__cause__ is original
    assert SECRET not in repr(caught.value)
    failure = classify_etl_failure(caught.value)
    original_failure = classify_etl_failure(original)
    assert (failure.category, failure.reason, failure.recovery, failure.message) == (
        original_failure.category,
        original_failure.reason,
        original_failure.recovery,
        original_failure.message,
    )
    assert failure.code == error_type.code
    assert SECRET not in str(failure.as_dict())
    assert SECRET not in str(custom_exception_handler(caught.value, {}).data)
    engine.dispose.assert_called_once()
    assert source._connection is source._engine is None
    if phase == "iterate":
        chunks.close.assert_called_once()


@pytest.mark.parametrize("phase", ["engine", "connect", "options", "probe"])
def test_postgres_unexpected_open_failure_releases_acquired_resources(monkeypatch, phase):
    source, engine, connection = fake_postgres(monkeypatch)
    primary = RuntimeError("unexpected failure")
    if phase == "engine":
        monkeypatch.setattr(postgres_source, "create_engine", Mock(side_effect=primary))
    elif phase == "connect":
        engine.connect.side_effect = primary
    elif phase == "options":
        connection.execution_options.side_effect = primary
    else:
        connection.execute.side_effect = primary
    with pytest.raises(RuntimeError) as caught:
        source.__enter__()
    assert caught.value is primary
    assert source._connection is source._engine is None
    assert engine.dispose.call_count == (phase != "engine")
    assert connection.close.call_count == (phase in ("options", "probe"))


def test_postgres_configuration_failure_has_safe_generic_domain_message(monkeypatch):
    from sqlalchemy.exc import ArgumentError

    primary = ArgumentError(SECRET)
    monkeypatch.setattr(postgres_source, "create_engine", Mock(side_effect=primary))
    with pytest.raises(ETLError) as caught:
        postgres_source.PostgresSource("invalid").__enter__()
    assert type(caught.value) is ETLError
    assert caught.value.__cause__ is primary
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize("during_connect", [False, True])
def test_postgres_unspecified_operational_error_is_not_assumed_connection_loss(
    monkeypatch, during_connect
):
    source, engine, connection = fake_postgres(monkeypatch)
    primary = OperationalError(SECRET, None, Exception(SECRET))
    if during_connect:
        engine.connect.side_effect = primary
    else:
        monkeypatch.setattr(postgres_source.pd, "read_sql", Mock(side_effect=primary))
    with pytest.raises(ETLError) as caught, source:
        list(source.extract())
    assert type(caught.value) is (SourceConnectionError if during_connect else ETLError)
    assert caught.value.__cause__ is primary


@pytest.mark.parametrize("primary", [None, RuntimeError("processing"), KeyboardInterrupt("stop")])
def test_postgres_cleanup_attempts_connection_and_engine_once(monkeypatch, primary):
    source, engine, connection = fake_postgres(monkeypatch)
    source.__enter__()
    close_error = OSError("close")
    connection.close.side_effect = close_error
    engine.dispose.side_effect = OSError("dispose")
    if primary is None:
        with pytest.raises(OSError) as caught:
            source.__exit__(None, None, None)
        assert caught.value is close_error
    else:
        source.__exit__(type(primary), primary, None)
    assert source._connection is source._engine is None
    source.__exit__(None, None, None)
    connection.close.assert_called_once()
    engine.dispose.assert_called_once()


def test_postgres_fake_success_preserves_values_metadata_and_chunk_cleanup(monkeypatch):
    source, engine, connection = fake_postgres(monkeypatch)
    data = frame()
    data.loc[0, "Quantity"] = -2
    chunks = Rows([data, data.copy()])
    read_sql = Mock(return_value=chunks)
    monkeypatch.setattr(postgres_source.pd, "read_sql", read_sql)
    with source:
        batches = list(source.extract())
    assert [(b.source_name, b.batch_number) for b in batches] == [
        ("postgres:raw_transactions", 0),
        ("postgres:raw_transactions", 1),
    ]
    for batch in batches:
        pd.testing.assert_frame_equal(batch.data, data)
        assert batch.extracted_at.tzinfo is not None
    assert batches[0].data is data
    assert read_sql.call_args.kwargs == {"chunksize": 50_000}
    connection.execution_options.assert_called_once_with(stream_results=True)
    chunks.close.assert_called_once()
    connection.close.assert_called_once()
    engine.dispose.assert_called_once()


@pytest.mark.parametrize("phase", ["open", "iterate"])
def test_csv_parser_failures_share_safe_contract(monkeypatch, phase):
    original = pd.errors.ParserError(SECRET)
    source = csv_source.CsvSource("orders.csv")
    if phase == "open":
        monkeypatch.setattr(csv_source.pd, "read_csv", Mock(side_effect=original))
    else:
        source._reader = Rows([frame()], failure=original)
    with pytest.raises(MalformedSourceError) as caught:
        if phase == "open":
            source._open("unused")
        else:
            list(source.extract())
    assert caught.value.__cause__ is original
    assert str(caught.value) == "Could not parse CSV source."


def test_excel_real_invalid_worksheet_xml_is_not_swallowed(tmp_path):
    from zipfile import ZipFile

    from .test_source_snapshots import workbook

    valid = workbook(tmp_path, {"Sheet": [CANONICAL_COLUMNS, [1] * 8]})
    broken = tmp_path / "broken.xlsx"
    with ZipFile(valid) as original, ZipFile(broken, "w") as output:
        for name in original.namelist():
            data = original.read(name)
            if name == "xl/worksheets/sheet1.xml":
                data = data.replace(b"</sheetData>", b"<invalid></sheetData>")
            output.writestr(name, data)
    with pytest.raises(MalformedSourceError):
        with excel_source.ExcelSource(broken, batch_size=1) as source:
            list(source.extract())
    assert source._workbook is source._stream is None


def test_excel_failed_load_does_not_close_borrowed_handle(monkeypatch):
    import io

    stream = io.BytesIO(b"borrowed")
    original = ParseError(SECRET)
    monkeypatch.setattr(excel_source.openpyxl, "load_workbook", Mock(side_effect=original))
    with pytest.raises(MalformedSourceError) as caught:
        excel_source.ExcelSource("orders.xlsx")._open(stream)
    assert caught.value.__cause__ is original
    assert not stream.closed
    stream.close()


def test_postgres_generator_close_surfaces_cleanup_failure(monkeypatch):
    source, engine, connection = fake_postgres(monkeypatch)
    failure = OSError("chunk close")
    chunks = Rows([frame()], cleanup=failure)
    monkeypatch.setattr(postgres_source.pd, "read_sql", Mock(return_value=chunks))
    with source:
        iterator = source.extract()
        next(iterator)
        with pytest.raises(OSError) as caught:
            iterator.close()
    assert caught.value is failure
    chunks.close.assert_called_once()
    engine.dispose.assert_called_once()


@pytest.mark.parametrize("kind", ["csv", "excel", "postgres"])
@pytest.mark.parametrize("missing", [False, True])
def test_source_diagnostics_omit_identifiers_and_preserve_batch_metadata(
    monkeypatch, caplog, tmp_path, kind, missing
):
    import logging

    from etl.exceptions import MissingColumnsError

    private = "review-private-source"
    sheet = "review-private-sheet"
    data = frame()
    if missing:
        data = data.drop(columns=["Price"])
    if kind == "csv":
        source = csv_source.CsvSource(tmp_path / (private + ".csv"))
        source._reader = Rows([data])
        logger = csv_source.logger
        name = f"csv:{private}.csv"
    elif kind == "excel":
        source = excel_source.ExcelSource(tmp_path / (private + ".xlsx"))
        source.sheet_names = [sheet]
        rows = Rows([tuple(data.columns), *data.itertuples(index=False, name=None)])
        source._workbook = {sheet: SimpleNamespace(iter_rows=lambda **kwargs: rows)}
        logger = excel_source.logger
        name = f"xlsx:{private}.xlsx:{sheet}"
    else:
        source, _, _ = fake_postgres(monkeypatch)
        source.table = private
        source._connection = Mock()
        monkeypatch.setattr(postgres_source.pd, "read_sql", Mock(return_value=Rows([data])))
        logger = postgres_source.logger
        name = f"postgres:{private}"

    with caplog.at_level(logging.INFO, logger=logger.name):
        if missing:
            with pytest.raises(MissingColumnsError) as caught:
                list(source.extract())
            assert private not in str(caught.value)
            assert sheet not in str(caught.value)
            assert str(tmp_path) not in str(caught.value)
            assert "Price" in str(caught.value)
        else:
            (batch,) = source.extract()
            assert (batch.source_name, batch.batch_number) == (name, 0)
            pd.testing.assert_frame_equal(batch.data, data)
    records = [record for record in caplog.records if record.name == logger.name]
    if not missing:
        assert len(records) == 1
        assert records[0].batch == 0 and records[0].rows == 1
    for record in records:
        assert not {"path", "sheet", "table"} & vars(record).keys()
        assert private not in repr(vars(record))
        assert sheet not in repr(vars(record))


def test_empty_read_only_excel_diagnostics_and_iterator_cleanup(tmp_path, caplog):
    import logging

    from .test_source_snapshots import workbook

    private_sheet = "review-private-sheet"
    path = workbook(tmp_path, {private_sheet: []})
    with caplog.at_level(logging.WARNING, logger=excel_source.logger.name):
        with excel_source.ExcelSource(path) as source:
            rows = source._workbook[private_sheet].iter_rows(values_only=True)
            assert callable(getattr(rows, "close", None))
            assert list(rows) == []
            rows.close()
            assert list(source.extract()) == []
    (record,) = [record for record in caplog.records if record.name == excel_source.logger.name]
    assert record.getMessage() == "excel_source.empty_sheet"
    assert not {"path", "sheet"} & vars(record).keys()
    assert str(path) not in repr(vars(record))
    assert private_sheet not in repr(vars(record))


def test_supported_pandas_iterator_cleanup_contract():
    import io
    from contextlib import ExitStack

    from pandas.io.sql import SQLDatabase

    reader = pd.read_csv(io.StringIO("Invoice,Price\n1,2\n"), chunksize=1)
    assert callable(getattr(reader, "close", None))
    reader.close()

    # This is the actual generator used by pandas' SQLAlchemy read_query path.
    # No database connection is created.
    result = Mock()
    result.fetchmany.return_value = []
    with ExitStack() as stack:
        chunks = SQLDatabase._query_iterator(result, stack, 1, ["Invoice", "Price"])
        assert callable(getattr(chunks, "close", None))
        assert len(list(chunks)) == 1
        chunks.close()
