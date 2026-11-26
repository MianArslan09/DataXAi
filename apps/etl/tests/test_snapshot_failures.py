"""Snapshot failure injection without database services or large datasets."""

from unittest.mock import Mock

import pytest

from etl import source_snapshots as snapshots
from etl.sources.csv_source import CsvSource

from .test_source_snapshots import FIXTURES
from .test_source_snapshots import store as store


def test_snapshot_parser_open_preserves_infrastructure_error(store, monkeypatch):
    identity = snapshots.capture_snapshot(FIXTURES / "online_retail_ii_sample.csv")
    primary = OSError("source read failed")
    monkeypatch.setattr(CsvSource, "_open", Mock(side_effect=primary))
    with pytest.raises(OSError) as caught:
        with snapshots.open_snapshot_csv(identity, filename="orders.csv"):
            pass
    assert caught.value is primary


def test_snapshot_close_failure_never_attests(store, monkeypatch):
    identity = snapshots.capture_snapshot(FIXTURES / "online_retail_ii_sample.csv")
    open_object = snapshots._open_object
    failure = OSError("final close failed")

    class FailingClose:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

        def close(self):
            self.stream.close()
            raise failure

    monkeypatch.setattr(snapshots, "_open_object", lambda *args: FailingClose(open_object(*args)))
    with pytest.raises(OSError) as caught:
        with snapshots.open_snapshot_csv(identity, filename="orders.csv") as traversal:
            list(traversal)
    assert caught.value is failure
    assert not traversal.verified_exhausted


def test_snapshot_consumer_primary_survives_parser_cleanup(store, monkeypatch):
    identity = snapshots.capture_snapshot(FIXTURES / "online_retail_ii_sample.csv")
    original_exit = CsvSource.__exit__

    def fail_close(self, *args):
        original_exit(self, *args)
        raise OSError("parser cleanup failed")

    monkeypatch.setattr(CsvSource, "__exit__", fail_close)
    primary = KeyboardInterrupt("consumer interruption")
    with pytest.raises(KeyboardInterrupt) as caught:
        with snapshots.open_snapshot_csv(
            identity, filename="orders.csv", batch_size=1
        ) as traversal:
            next(iter(traversal))
            raise primary
    assert caught.value is primary
    assert not traversal.verified_exhausted


@pytest.mark.parametrize("kind", ["io", "bug", "interrupt"])
def test_snapshot_iteration_preserves_primary_despite_cleanup(store, monkeypatch, kind):
    errors = {
        "io": OSError("read"),
        "bug": ValueError("bug"),
        "interrupt": KeyboardInterrupt("stop"),
    }
    primary = errors[kind]
    identity = snapshots.capture_snapshot(FIXTURES / "online_retail_ii_sample.csv")
    original_extract = CsvSource.extract
    original_exit = CsvSource.__exit__

    def broken(self):
        batches = original_extract(self)
        try:
            yield next(batches)
            raise primary
        finally:
            batches.close()

    def close(self, *args):
        original_exit(self, *args)
        raise OSError("secondary close")

    monkeypatch.setattr(CsvSource, "extract", broken)
    monkeypatch.setattr(CsvSource, "__exit__", close)
    with pytest.raises(type(primary)) as caught:
        with snapshots.open_snapshot_csv(
            identity, filename="orders.csv", batch_size=1
        ) as traversal:
            list(traversal)
    assert caught.value is primary
    assert not traversal.verified_exhausted
    assert (store / identity.sha256).is_file()


def test_snapshot_parser_cleanup_only_failure_blocks_verification_and_allows_reopen(
    store, monkeypatch
):
    identity = snapshots.capture_snapshot(FIXTURES / "online_retail_ii_sample.csv")
    original_exit = CsvSource.__exit__
    primary = OSError("parser close")

    def close(self, *args):
        original_exit(self, *args)
        raise primary

    with monkeypatch.context() as patch:
        patch.setattr(CsvSource, "__exit__", close)
        with pytest.raises(OSError) as caught:
            with snapshots.open_snapshot_csv(identity, filename="orders.csv") as traversal:
                list(traversal)
        assert caught.value is primary
        assert not traversal.verified_exhausted
        assert traversal.postcheck_seconds is None
    with snapshots.open_snapshot_csv(identity, filename="orders.csv") as retry:
        assert sum(len(batch) for batch in retry) == 18
    assert retry.verified_exhausted


@pytest.mark.parametrize("interrupted", [False, True])
def test_capture_unlink_failure_preserves_primary_or_published_bytes(
    store, tmp_path, monkeypatch, interrupted
):
    import hashlib
    from pathlib import Path

    data = b"immutable snapshot"
    source = tmp_path / "source.csv"
    source.write_bytes(data)
    primary = KeyboardInterrupt("capture interrupted")
    unlink = Path.unlink
    attempts = []

    def fail_temp_unlink(path, *args, **kwargs):
        if path.parent == store and path.name.startswith(".capture-"):
            attempts.append(path)
            raise OSError("temporary unlink failed")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_temp_unlink)
        if interrupted:
            patch.setattr(snapshots.os, "fsync", Mock(side_effect=primary))
        with pytest.raises(KeyboardInterrupt if interrupted else snapshots.SnapshotError) as caught:
            snapshots.capture_snapshot(source)
        if interrupted:
            assert caught.value is primary
    assert len(attempts) == 1
    published = store / hashlib.sha256(data).hexdigest()
    assert published.exists() is not interrupted
    assert attempts[0].read_bytes() == data
    if not interrupted:
        assert published.read_bytes() == data
        info = published.stat()
        assert snapshots.capture_snapshot(source).sha256 == published.name
        assert snapshots.os.path.samestat(info, published.stat())


def test_capture_integrity_error_survives_failed_temp_unlink(store, tmp_path, monkeypatch):
    from pathlib import Path

    source = tmp_path / "source.csv"
    source.write_bytes(b"data")
    primary = snapshots.SnapshotError("custody failure")
    unlink = Path.unlink

    def fail_temp(path, *args, **kwargs):
        if path.parent == store and path.name.startswith(".capture-"):
            raise OSError("unlink")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(snapshots, "_object_custody", Mock(side_effect=primary))
    monkeypatch.setattr(Path, "unlink", fail_temp)
    with pytest.raises(snapshots.SnapshotError) as caught:
        snapshots.capture_snapshot(source)
    assert caught.value is primary
    assert all(path.name.startswith(".capture-") for path in store.iterdir())


def test_fdopen_failure_preserves_primary_and_closes_descriptor(tmp_path, monkeypatch):
    import os

    fd = os.open(tmp_path / "owned", os.O_WRONLY | os.O_CREAT)
    primary = RuntimeError("fdopen failure")
    real_close = os.close
    closed = []

    def close(descriptor):
        real_close(descriptor)
        closed.append(descriptor)
        raise OSError("secondary close")

    with monkeypatch.context() as patch:
        patch.setattr(snapshots.os, "fdopen", Mock(side_effect=primary))
        patch.setattr(snapshots.os, "close", close)
        with pytest.raises(RuntimeError) as caught:
            snapshots._fdopen(fd, "wb")
    assert caught.value is primary
    assert closed == [fd]
    with pytest.raises(OSError):
        os.fstat(fd)


def test_open_object_identity_error_survives_close_failure(store, monkeypatch):
    identity = snapshots.capture_snapshot(FIXTURES / "online_retail_ii_sample.csv")
    fdopen = snapshots._fdopen
    closed = []

    def wrapped(fd, mode):
        stream = fdopen(fd, mode)

        class Handle:
            def fileno(self):
                return stream.fileno()

            def close(self):
                stream.close()
                closed.append(stream.closed)
                raise OSError("close")

        return Handle()

    monkeypatch.setattr(snapshots, "_fdopen", wrapped)
    monkeypatch.setattr(snapshots.os.path, "samestat", lambda *args: False)
    with pytest.raises(snapshots.SnapshotError, match="changed during opening"):
        snapshots._open_object(*snapshots._root(), identity)
    assert closed == [True]
    assert (store / identity.sha256).is_file()


def test_completion_service_does_not_seal_after_parser_cleanup_failure(store, monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4

    from etl import ingestion_completion as service

    identity = snapshots.capture_snapshot(FIXTURES / "online_retail_ii_sample.csv")
    scope = service.CsvScope("orders.csv", batch_size=5)
    proof = SimpleNamespace(
        ingestion=SimpleNamespace(status="OPEN"),
        sealed_at=None,
        execution_key=uuid4(),
        transformation_version="unchanged",
        binding_digest="b" * 64,
    )
    manager = Mock()
    manager.select_related.return_value.get.return_value = proof
    monkeypatch.setattr(service, "IngestionCompletionProof", SimpleNamespace(objects=manager))
    monkeypatch.setattr(service, "_check_entry", Mock())
    monkeypatch.setattr(service, "_verify_binding", Mock(return_value=(identity, scope, None)))
    processed = []

    def stage(*, ingestion_id, batch):
        processed.append(batch.batch_number)
        return SimpleNamespace(row_count=len(batch)), "a" * 64

    def run(**kwargs):
        return SimpleNamespace(
            status=service.PipelineRun.Status.SUCCEEDED,
            records_ingested=5 if kwargs["batch_number"] < 3 else 3,
        )

    seal = Mock(side_effect=AssertionError("Completion sealing reached after failed traversal"))
    monkeypatch.setattr(service, "_stage_with_digest", stage)
    monkeypatch.setattr(service, "run_staged_batch", run)
    monkeypatch.setattr(service, "_seal", seal)
    original_exit = CsvSource.__exit__

    def close(self, *args):
        original_exit(self, *args)
        raise OSError("cleanup-only failure")

    monkeypatch.setattr(CsvSource, "__exit__", close)
    with pytest.raises(OSError, match="cleanup-only failure"):
        service.complete_bound_ingestion(ingestion_id=uuid4(), claim_ttl_seconds=60)
    assert processed == [0, 1, 2, 3]
    seal.assert_not_called()
    assert proof.ingestion.status == "OPEN" and proof.sealed_at is None


@pytest.mark.parametrize("primary", [None, RuntimeError("sync bug")])
def test_directory_sync_cleanup_does_not_hide_primary(monkeypatch, primary):
    failure = OSError("directory close")
    monkeypatch.setattr(snapshots.os, "O_DIRECTORY", 0, raising=False)
    monkeypatch.setattr(snapshots.os, "O_NOFOLLOW", 0, raising=False)
    monkeypatch.setattr(snapshots.os, "open", Mock(return_value=123))
    sync = Mock(side_effect=primary)
    close = Mock(side_effect=failure)
    monkeypatch.setattr(snapshots.os, "fsync", sync)
    monkeypatch.setattr(snapshots.os, "close", close)
    with pytest.raises(type(primary) if primary else snapshots.SnapshotError) as caught:
        snapshots._sync_published_directory("unused")
    if primary:
        assert caught.value is primary
    else:
        assert "durability is unconfirmed" in str(caught.value)
    close.assert_called_once_with(123)


@pytest.mark.skipif(snapshots.os.name != "nt", reason="Windows handle ownership path.")
def test_windows_handle_conversion_failure_survives_unlink_failure(store, monkeypatch):
    import ctypes
    import msvcrt
    from pathlib import Path

    primary = RuntimeError("handle conversion failed")
    original_close = ctypes.windll.kernel32.CloseHandle
    closed = []
    unlink = Path.unlink

    def close(handle):
        result = original_close(handle)
        closed.append(result)
        return result

    path = store / ".capture-unit-failure"

    def fail_unlink(target, *args, **kwargs):
        if target == path:
            raise OSError("temporary unlink failed")
        return unlink(target, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(msvcrt, "open_osfhandle", Mock(side_effect=primary))
        patch.setattr(ctypes.windll.kernel32, "CloseHandle", close)
        patch.setattr(Path, "unlink", fail_unlink)
        with pytest.raises(RuntimeError) as caught:
            snapshots._windows_fd(path, create_new=True)
    assert caught.value is primary
    assert closed == [1]
    assert path.is_file()
    path.unlink()


def test_snapshot_partial_traversal_cleanup_failure_is_not_silently_ignored(store, monkeypatch):
    identity = snapshots.capture_snapshot(FIXTURES / "online_retail_ii_sample.csv")
    original_extract = CsvSource.extract
    primary = OSError("generator cleanup")

    def batches(self):
        wrapped = original_extract(self)
        try:
            yield from wrapped
        finally:
            wrapped.close()
            raise primary

    monkeypatch.setattr(CsvSource, "extract", batches)
    with pytest.raises(OSError) as caught:
        with snapshots.open_snapshot_csv(
            identity, filename="orders.csv", batch_size=1
        ) as traversal:
            next(iter(traversal))
    assert caught.value is primary
    assert not traversal.verified_exhausted
