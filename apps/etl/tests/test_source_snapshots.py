import csv
import hashlib
import io
import os
import stat
import subprocess
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from threading import Barrier
from zipfile import ZipFile

import openpyxl
import pandas as pd
import pytest

from etl import source_snapshots as snapshots
from etl.exceptions import MalformedSourceError, MissingColumnsError
from etl.sources.contract import CANONICAL_COLUMNS
from etl.sources.csv_source import CsvSource
from etl.sources.excel_source import ExcelSource

FIXTURES = Path(__file__).parent / "fixtures"
HEADER = ",".join(CANONICAL_COLUMNS)


@pytest.fixture
def store(settings, tmp_path):
    root = tmp_path / "store"
    root.mkdir(mode=0o700)
    settings.ETL_SNAPSHOT_ROOT = root
    return root


def csv_file(tmp_path, data):
    path = tmp_path / "orders.csv"
    path.write_bytes(data)
    return path


def workbook(tmp_path, scopes):
    book = openpyxl.Workbook()
    book.remove(book.active)
    for name, rows in scopes.items():
        sheet = book.create_sheet(name)
        for row in rows:
            sheet.append(row)
    path = tmp_path / "orders.xlsx"
    book.save(path)
    book.close()
    return path


def assert_batches_equal(expected, actual):
    assert len(expected) == len(actual)
    for left, right in zip(expected, actual, strict=True):
        assert (left.source_name, left.batch_number) == (right.source_name, right.batch_number)
        pd.testing.assert_frame_equal(left.data, right.data)


def test_capture_reuse_checks_existing_bytes_and_length(store, tmp_path):
    data = b"x" * (snapshots._CHUNK + 17)
    path = csv_file(tmp_path, data)
    identity = snapshots.capture_snapshot(path)
    assert identity == snapshots.SourceSnapshot(hashlib.sha256(data).hexdigest(), len(data))
    obj = store / identity.sha256
    original = obj.stat()
    assert snapshots.capture_snapshot(path) == identity
    assert os.path.samestat(original, obj.stat())
    assert list(store.iterdir()) == [obj]
    if os.name == "posix":
        assert obj.stat().st_mode & 0o222 == 0
    rewrite_object(obj, b"y" * len(data))
    with pytest.raises(snapshots.SnapshotError, match="digest or length"):
        snapshots.capture_snapshot(path)
    assert obj.read_bytes() == b"y" * len(data)
    assert list(store.iterdir()) == [obj]


def rewrite_object(obj, data):
    # A trusted publisher can simulate persistent corruption.
    if os.name == "posix":
        obj.chmod(0o640)
    obj.write_bytes(data)
    if os.name == "posix":
        obj.chmod(0o440)


@pytest.mark.parametrize("operation", ["fsync", "link"])
def test_interrupted_publication_cleans_only_owned_temp(store, tmp_path, monkeypatch, operation):
    sentinel = store / ".capture-another-publisher"
    sentinel.write_bytes(b"other")

    def fail(*args):
        raise OSError("private paths must not leak")

    monkeypatch.setattr(snapshots.os, operation, fail)
    with pytest.raises(snapshots.SnapshotError) as caught:
        snapshots.capture_snapshot(csv_file(tmp_path, b"safe"))
    assert "private paths" not in str(caught.value)
    assert list(store.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"other"


@pytest.mark.skipif(os.name != "posix", reason="Requires POSIX directory fsync.")
def test_directory_sync_failure_leaves_verified_retry(store, tmp_path, monkeypatch):
    data = b"captured bytes"
    path = csv_file(tmp_path, data)
    obj = store / hashlib.sha256(data).hexdigest()
    file_sync = os.fsync
    events = []

    def sync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            assert list(store.iterdir()) == [obj]
            events.append("directory")
            if events.count("directory") == 1:
                raise OSError("simulated directory sync failure")
        else:
            events.append("file")
        file_sync(fd)

    monkeypatch.setattr(os, "fsync", sync)
    with pytest.raises(snapshots.SnapshotError, match="durability is unconfirmed"):
        snapshots.capture_snapshot(path)
    published = obj.stat()
    assert obj.read_bytes() == data
    assert snapshots.capture_snapshot(path).sha256 == obj.name
    assert os.path.samestat(published, obj.stat())
    assert events == ["file", "directory", "file", "directory"]


def test_competing_publishers_do_not_replace_object(store, tmp_path, monkeypatch):
    start = Barrier(2, timeout=10)
    link = os.link
    wins = []

    def racing_link(src, dst):
        start.wait()
        link(src, dst)
        wins.append(dst.stat())

    monkeypatch.setattr(snapshots.os, "link", racing_link)
    path = csv_file(tmp_path, b"identical")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(snapshots.capture_snapshot, [path, path])
    assert first == second
    (obj,) = store.iterdir()
    assert len(wins) == 1 and os.path.samestat(wins[0], obj.stat())
    assert obj.read_bytes() == b"identical"


@pytest.mark.parametrize("suffix", ["csv", "xlsx"])
def test_handle_parity_and_ownership(store, monkeypatch, suffix):
    path = FIXTURES / f"online_retail_ii_sample.{suffix}"
    if suffix == "csv":
        adapter, traversal = CsvSource, snapshots.open_snapshot_csv
    else:
        adapter, traversal = ExcelSource, snapshots.open_snapshot_excel
    with adapter(path, batch_size=4) as source:
        expected = list(source.extract())
    identity = snapshots.capture_snapshot(path)
    handles = []
    verify = snapshots._check_identity

    def inspect(handle, *args):
        assert not handle.closed
        handles.append(handle)
        return verify(handle, *args)

    monkeypatch.setattr(snapshots, "_check_identity", inspect)
    with traversal(identity, filename=path.name, batch_size=4) as result:
        actual = list(result)
        assert not result.verified_exhausted
    assert_batches_equal(expected, actual)
    assert result.verified_exhausted
    assert len(handles) == 2 and handles[0] is handles[1] and handles[0].closed


@pytest.mark.parametrize(
    "data,error",
    [
        ("", MalformedSourceError),
        ("1,2,3,4,5,6,7,8\n", MissingColumnsError),
        ("Invoice,Price\n", MissingColumnsError),
        (HEADER + ",Price\n", MalformedSourceError),
        (HEADER + ",\n", MalformedSourceError),
        (HEADER + '\n1,2,"unterminated', MalformedSourceError),
        (HEADER + "\n1,2,3,4,5,6,7,8\n1,2,3,4,5,6,7,8,9", MalformedSourceError),
    ],
)
def test_bound_csv_rejects_invalid_input(store, tmp_path, data, error):
    identity = snapshots.capture_snapshot(csv_file(tmp_path, data.encode()))
    with (
        pytest.raises(error),
        snapshots.open_snapshot_csv(identity, filename="orders.csv") as result,
    ):
        list(result)


def test_header_only_csv_matches_pandas_and_validates_header(store, tmp_path):
    path = csv_file(tmp_path, (HEADER + "\r\n").encode("utf-16"))
    with CsvSource(path, encoding="utf-16") as source:
        expected = list(source.extract())
    identity = snapshots.capture_snapshot(path)
    with snapshots.open_snapshot_csv(identity, filename=path.name, encoding="utf-16") as traversal:
        actual = list(traversal)
    assert_batches_equal(expected, actual)
    assert sum(map(len, actual)) == 0 and traversal.verified_exhausted


def test_quoted_csv_and_replaced_original(store, tmp_path):
    text = HEADER + '\r\n1,2,"é,\r\n東京",4,5,6,7,8\r\n'
    path = csv_file(tmp_path, text.encode("utf-8-sig"))
    with CsvSource(path) as source:
        expected = list(source.extract())
    identity = snapshots.capture_snapshot(path)
    path.write_bytes(b"different upstream")
    with snapshots.open_snapshot_csv(identity, filename=path.name) as traversal:
        assert_batches_equal(expected, list(traversal))
    assert traversal.verified_exhausted


@pytest.mark.parametrize(
    "fields",
    [
        ["x" * 150_000, "small"],
        ["a" * 600_000, "b" * 600_000],
        ['multiline,\r\n"quoted" ' + "é" * 150_000, "東京"],
    ],
    ids=["large-field", "long-physical-line", "large-quoted-multiline"],
)
def test_large_csv_matches_path_reader(store, tmp_path, fields):
    path = tmp_path / "orders.csv"
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(CANONICAL_COLUMNS)
        writer.writerow([1, *fields, 4, 5, 6, 7, 8])
        writer.writerow(range(8))
    with CsvSource(path, batch_size=1) as source:
        expected = list(source.extract())
    assert expected[0].data.iloc[0, 1:3].tolist() == fields
    limit = csv.field_size_limit()
    identity = snapshots.capture_snapshot(path)
    with snapshots.open_snapshot_csv(identity, filename=path.name, batch_size=1) as traversal:
        assert_batches_equal(expected, list(traversal))
    assert traversal.verified_exhausted and csv.field_size_limit() == limit


def test_wide_csv_line_with_individually_small_fields(store, tmp_path):
    # Isolate the old physical-line ceiling from the independent csv.reader field limit.
    fields = ["a" * 120_000] * 10
    path = csv_file(tmp_path, (HEADER + ",Extra1,Extra2\n" + ",".join(fields)).encode())
    with CsvSource(path) as source:
        expected = list(source.extract())
    identity = snapshots.capture_snapshot(path)
    with snapshots.open_snapshot_csv(identity, filename=path.name) as traversal:
        assert_batches_equal(expected, list(traversal))
    assert traversal.verified_exhausted


def test_csv_chunk_boundaries_preserve_quote_semantics(store, tmp_path, monkeypatch):
    # Split escaped quotes, quoted CRLFs and multibyte characters across reads.
    monkeypatch.setattr(snapshots, "_CHUNK", 1)
    text = "\r\n" + HEADER + ',"extra,\r\n""name"\r\n1,2,"é,\r\n""東京""",4,5,6,7,8,\r\n'
    text += '1,2,a"b,4,5,6,7,8,end'
    path = csv_file(tmp_path, text.encode("utf-8-sig"))
    with CsvSource(path, batch_size=1) as source:
        expected = list(source.extract())
    identity = snapshots.capture_snapshot(path)
    with snapshots.open_snapshot_csv(identity, filename=path.name, batch_size=1) as traversal:
        assert_batches_equal(expected, list(traversal))
    assert traversal.verified_exhausted


@pytest.mark.parametrize(
    "ending",
    [",4,5,6,7", ",4,5,6,7,8,9", "text,4,5,6,7,8"],
    ids=["short-row", "surplus-field", "text-after-closing-quote"],
)
def test_invalid_record_after_large_quoted_field(ending):
    data = HEADER + '\n1,2,"' + "x" * 160_000 + '"' + ending
    with pytest.raises(MalformedSourceError):
        snapshots._csv_count(io.BytesIO(data.encode()), "utf-8")


def test_csv_budget_applies_only_to_logical_header(monkeypatch):
    header = HEADER + ',"extra\nname"'
    monkeypatch.setattr(snapshots, "_MAX_CSV_HEADER_CHARS", len(header))
    data = "\n" + header + "\r\n1,2," + "x" * 150_000 + ",4,5,6,7,8,9"
    assert snapshots._csv_count(io.BytesIO(data.encode()), "utf-8") == 1
    longer_header = header.replace("name", "names")
    with pytest.raises(MalformedSourceError, match="header exceeds"):
        snapshots._csv_count(io.BytesIO(longer_header.encode()), "utf-8")


def test_excel_selected_order_and_visited_header_only_sheet(store, tmp_path):
    path = workbook(
        tmp_path, {"Empty": [CANONICAL_COLUMNS], "Data": [CANONICAL_COLUMNS, list(range(8))]}
    )
    identity = snapshots.capture_snapshot(path)
    with snapshots.open_snapshot_excel(
        identity, filename=path.name, sheet_names=["Data", "Empty"]
    ) as scope:
        assert [batch.source_name for batch in scope] == ["xlsx:orders.xlsx:Data"]
    assert (
        scope.selected_sheets == scope.entered_sheets == scope.completed_sheets == ("Data", "Empty")
    )
    assert scope.verified_exhausted


@pytest.mark.parametrize("selection", [[], ["Blank"], ["Data", "Data"], ["Missing"]])
def test_invalid_excel_scope(store, tmp_path, selection):
    identity = snapshots.capture_snapshot(
        workbook(tmp_path, {"Data": [CANONICAL_COLUMNS], "Blank": []})
    )
    with (
        pytest.raises((MalformedSourceError, MissingColumnsError)),
        snapshots.open_snapshot_excel(
            identity, filename="orders.xlsx", sheet_names=selection
        ) as traversal,
    ):
        list(traversal)


@pytest.mark.parametrize("extra", ["Price", None, 17])
def test_header_only_excel_requires_unambiguous_labels(store, tmp_path, extra):
    # A later value fixes the physical column width even for a null header cell.
    header = [*CANONICAL_COLUMNS, extra, "Last"]
    path = workbook(tmp_path, {"Empty": [header]})
    snapshot = snapshots.capture_snapshot(path)
    with (
        pytest.raises(MalformedSourceError),
        snapshots.open_snapshot_excel(snapshot, filename=path.name) as traversal,
    ):
        list(traversal)


@pytest.mark.parametrize("dimension", [b"A1:H1", b"A1:G2"])
def test_underreported_excel_dimensions_cannot_hide_data(store, tmp_path, dimension):
    path = workbook(tmp_path, {"Sheet": [CANONICAL_COLUMNS, list(range(8))]})
    bad = tmp_path / "bad.xlsx"
    with ZipFile(path) as original, ZipFile(bad, "w") as changed:
        for name in original.namelist():
            data = original.read(name)
            if name == "xl/worksheets/sheet1.xml":
                assert b"A1:H2" in data
                data = data.replace(b"A1:H2", dimension)
            changed.writestr(name, data)
    with (
        pytest.raises(MalformedSourceError),
        snapshots.open_snapshot_excel(
            snapshots.capture_snapshot(bad), filename="orders.xlsx"
        ) as traversal,
    ):
        list(traversal)


@pytest.mark.parametrize("raise_error", [False, True])
def test_interrupted_traversal_closes_handle_without_attesting(store, monkeypatch, raise_error):
    path = FIXTURES / "online_retail_ii_sample.xlsx"
    identity = snapshots.capture_snapshot(path)
    handles = []
    check = snapshots._check_identity

    def observe(handle, *args):
        handles.append(handle)
        return check(handle, *args)

    monkeypatch.setattr(snapshots, "_check_identity", observe)
    interruption = (
        pytest.raises(ValueError, match="consumer interruption") if raise_error else nullcontext()
    )
    with (
        interruption,
        snapshots.open_snapshot_excel(identity, filename=path.name, batch_size=1) as traversal,
    ):
        next(iter(traversal))
        if raise_error:
            raise ValueError("consumer interruption")
    assert not traversal.verified_exhausted
    assert traversal.entered_sheets == traversal.selected_sheets[:1]
    assert traversal.completed_sheets == ()
    assert len(handles) == 1 and handles[0].closed
    assert list(traversal) == []


def test_postcheck_detects_in_place_changes(store, tmp_path):
    data = (HEADER + "\n1,2,3,4,5,6,7,8\n").encode()
    identity = snapshots.capture_snapshot(csv_file(tmp_path, data))
    with (
        pytest.raises(snapshots.SnapshotError, match="digest or length"),
        snapshots.open_snapshot_csv(identity, filename="orders.csv") as traversal,
    ):
        list(traversal)
        rewrite_object(store / identity.sha256, data.replace(b"1,2", b"2,2"))
    assert not traversal.verified_exhausted


def test_postcheck_rejects_replaced_directory_entry_even_for_identical_bytes(store, tmp_path):
    data = (HEADER + "\n").encode()
    identity = snapshots.capture_snapshot(csv_file(tmp_path, data))
    with (
        pytest.raises(snapshots.SnapshotError, match="changed during traversal"),
        snapshots.open_snapshot_csv(identity, filename="orders (2026).csv") as traversal,
    ):
        list(traversal)
        replacement = store / "replacement"
        replacement.write_bytes(data)
        if os.name == "posix":
            replacement.chmod(0o440)
        # Win32 replacement of an open target can fail even with delete sharing.
        # Move its name aside first; the verified reader still holds that object.
        (store / identity.sha256).rename(store / "original")
        replacement.rename(store / identity.sha256)
    assert not traversal.verified_exhausted


def test_root_rejects_linked_ancestor(settings, tmp_path):
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    root = real / "store"
    root.mkdir(mode=0o700)
    link = tmp_path / "alias"
    if os.name == "nt":
        # A directory junction does not require the Windows symlink privilege.
        import _winapi

        _winapi.CreateJunction(str(real), str(link))
    else:
        link.symlink_to(real, target_is_directory=True)
    settings.ETL_SNAPSHOT_ROOT = link / "store"
    with pytest.raises(snapshots.SnapshotError, match="reparse"):
        snapshots.capture_snapshot(csv_file(tmp_path, b"blocked"))
    assert not list(root.iterdir())


def test_bad_length_rejected_before_parser_opens(store, tmp_path, monkeypatch):
    path = csv_file(tmp_path, HEADER.encode())
    good = snapshots.capture_snapshot(path)
    monkeypatch.setattr(
        CsvSource, "_open", lambda *_: pytest.fail("parser opened before verification")
    )
    wrong = snapshots.SourceSnapshot(good.sha256, good.size_bytes + 1)
    with (
        pytest.raises(snapshots.SnapshotError, match="digest or length"),
        snapshots.open_snapshot_csv(wrong, filename=path.name),
    ):
        pass


def test_refuse_unsafe_roots_paths_and_labels(settings, tmp_path):
    path = csv_file(tmp_path, b"unused")
    for root in ("", "relative", settings.BASE_DIR, tmp_path / "missing"):
        settings.ETL_SNAPSHOT_ROOT = root
        with pytest.raises(snapshots.SnapshotError):
            snapshots.capture_snapshot(path)
    settings.ETL_SNAPSHOT_ROOT = tmp_path
    settings.MEDIA_ROOT = tmp_path
    with pytest.raises(snapshots.SnapshotError, match="overlaps"):
        snapshots.capture_snapshot(path)
    with pytest.raises(snapshots.SnapshotError, match="digest"):
        snapshots.SourceSnapshot("../object", 0)
    with pytest.raises(snapshots.SnapshotError, match="filename"):
        snapshots.open_snapshot_csv(snapshots.SourceSnapshot("0" * 64, 0), filename="../data.csv")


def test_reject_writable_store(store, tmp_path):
    if os.name == "nt":
        subprocess.run(
            ["icacls", str(store), "/grant", "*S-1-1-0:(M)"], check=True, capture_output=True
        )
    else:
        store.chmod(0o770)
    with pytest.raises(snapshots.SnapshotError):
        snapshots.capture_snapshot(csv_file(tmp_path, b"secret"))
