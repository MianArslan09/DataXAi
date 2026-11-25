"""Custody of local CSV/XLSX snapshots. No database or ingestion lifecycle operations.

Provision ETL_SNAPSHOT_ROOT on a trusted local hard-link-capable filesystem. Protect
its ancestors against replacement. Use a dedicated publisher/owner and distinct,
unprivileged parser accounts with read/traverse access only. On POSIX, use a private
publisher directory or a setgid reader-group directory (2750). On Windows provision
NTFS DACLs for the owner, SYSTEM, administrators and a named read-only parser group.
Ordinary workers must not own the root/objects, belong to publisher/admin principals,
or hold ACL/backup/restore privileges. Another service must not serve this directory.
The owner controls retention and backup. Hashes detect persistent changes; even two
matching hashes cannot exclude transient tampering by a privileged writer.
"""

import codecs
import hashlib
import io
import os
import re
import stat
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from time import perf_counter
from uuid import uuid4

from django.conf import settings

from etl.exceptions import ETLError, MalformedSourceError, MissingColumnsError
from etl.sources.contract import CANONICAL_COLUMNS, SourceBatch
from etl.sources.csv_source import CsvSource
from etl.sources.excel_source import ExcelSource

_CHUNK = 1024 * 1024
# Only the header needs to be retained (canonical and duplicate column checks).
# Bound a malformed or unterminated header independently of transaction data.
_MAX_CSV_HEADER_CHARS = 8 * 1024 * 1024
_CSV_TOKENS = re.compile(r'[^,"\r\n]+|[,"\r\n]')


class SnapshotError(ETLError):
    code = "source_snapshot_error"


@dataclass(frozen=True)
class SourceSnapshot:
    sha256: str
    size_bytes: int

    def __post_init__(self):
        if type(self.sha256) is not str or not re.fullmatch("[0-9a-f]{64}", self.sha256):
            raise SnapshotError("Invalid snapshot digest.")
        if type(self.size_bytes) is not int or self.size_bytes < 0:
            raise SnapshotError("Invalid snapshot length.")


def _plain_stat(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or (
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise SnapshotError("Links and reparse points are unsupported.")
    return info


def _check_path(path):
    if ".." in path.parts or (
        os.name == "nt"
        and (
            path.drive.startswith("\\\\")
            or any(
                ":" in p or p.endswith((" ", ".")) or PureWindowsPath(p).is_reserved()
                for p in path.parts[1:]
            )
        )
    ):
        raise SnapshotError("Unsupported snapshot path.")
    for parent in reversed(path.parents):
        _plain_stat(parent)
    return _plain_stat(path)


def _windows_custody(path, publisher):
    """Check the actual owner and allow ACEs. Unknown ACL semantics fail closed."""
    import ctypes as c

    api = c.windll.advapi32
    owner, acl, desc = c.c_void_p(), c.c_void_p(), c.c_void_p()
    # SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION.
    if api.GetNamedSecurityInfoW(
        str(path), 1, 5, c.byref(owner), None, c.byref(acl), None, c.byref(desc)
    ):
        raise SnapshotError("Could not verify snapshot permissions.")

    def sid_bytes(pointer):
        return c.string_at(pointer, api.GetLengthSid(pointer))

    def trusted(pointer):
        # WinLocalSystemSid, WinBuiltinAdministratorsSid.
        return (
            sid_bytes(pointer) == publisher
            or api.IsWellKnownSid(pointer, 22)
            or api.IsWellKnownSid(pointer, 26)
        )

    def broad_principal(pointer):
        # Everyone, Anonymous, Authenticated Users, BUILTIN Users or Guests.
        return any(api.IsWellKnownSid(pointer, kind) for kind in (1, 13, 17, 27, 28))

    try:
        if not owner or broad_principal(owner):
            raise SnapshotError("Snapshot requires a dedicated trusted owner.")
        if publisher is None:
            publisher = sid_bytes(owner)
        if not acl or not trusted(owner):
            raise SnapshotError("Untrusted snapshot owner or absent DACL.")
        ace_count = c.c_ushort.from_address(acl.value + 4).value
        for index in range(ace_count):
            ace = c.c_void_p()
            if not api.GetAce(acl, index, c.byref(ace)):
                raise SnapshotError("Unreadable snapshot access rule.")
            kind, flags = c.string_at(ace, 2)
            if kind == 1:  # ACCESS_DENIED_ACE
                continue
            if kind != 0:
                raise SnapshotError("Unsupported snapshot access rule.")
            mask = c.c_uint32.from_address(ace.value + 4).value
            sid = c.c_void_p(ace.value + 8)
            if flags & 8 and api.IsWellKnownSid(sid, 3):  # Inherit-only CREATOR OWNER
                continue
            # S-1-3-4 (OWNER RIGHTS) grants rights to the verified owner only.
            if sid_bytes(sid) == bytes.fromhex("01 01 000000000003 04000000"):
                continue
            if broad_principal(sid):
                raise SnapshotError("Snapshot access must be restricted to trusted accounts.")
            # FILE_GENERIC_READ | FILE_GENERIC_EXECUTE, GENERIC_READ/EXECUTE.
            if mask & ~0xA01200A9 and not trusted(sid):
                raise SnapshotError("Snapshot permits writes by an untrusted principal.")
        return publisher
    finally:
        c.windll.kernel32.LocalFree(desc)


def _root():
    value = settings.ETL_SNAPSHOT_ROOT
    root = Path(value) if value else None
    if root is None or not root.is_absolute() or root == root.parent:
        raise SnapshotError("Configure an absolute private snapshot directory.")
    info = _check_path(root)
    if not stat.S_ISDIR(info.st_mode):
        raise SnapshotError("Snapshot root must be an existing directory.")
    excluded = [settings.BASE_DIR, settings.STATIC_ROOT, settings.MEDIA_ROOT]
    excluded.extend(p[1] if isinstance(p, (list, tuple)) else p for p in settings.STATICFILES_DIRS)
    resolved = root.resolve()
    for location in filter(None, excluded):
        location = Path(location).resolve()
        if resolved.is_relative_to(location) or location.is_relative_to(resolved):
            raise SnapshotError("Snapshot root overlaps a repository or served directory.")
    if os.name == "nt":
        import ctypes

        if ctypes.windll.kernel32.GetDriveTypeW(root.anchor) != 3:  # DRIVE_FIXED
            raise SnapshotError("A local snapshot volume is required.")
        return root, _windows_custody(root, None)
    if os.name != "posix" or info.st_mode & 0o027:
        raise SnapshotError("Unsupported or unsafe snapshot directory permissions.")
    return root, info.st_uid


def _object_custody(path, publisher):
    info = _plain_stat(path)
    if not stat.S_ISREG(info.st_mode):
        raise SnapshotError("Snapshot must be a regular file.")
    if os.name == "nt":
        _windows_custody(path, publisher)
    elif info.st_uid != publisher or info.st_mode & 0o227:
        raise SnapshotError("Unsafe snapshot object permissions.")
    return info


def _windows_fd(path, *, create_new):
    # Exclusive CREATE_NEW protects partial bytes. Share delete on published
    # objects so a competing publisher may unlink its private temporary name.
    import ctypes
    import msvcrt

    create = ctypes.windll.kernel32.CreateFileW
    create.restype = ctypes.c_void_p
    access, share, disposition = (0x40000000, 0, 1) if create_new else (0x80000000, 7, 3)
    handle = create(str(path), access, share, None, disposition, 0x00200080, None)
    if handle == ctypes.c_void_p(-1).value:
        raise SnapshotError("Could not acquire snapshot file handle.")
    try:
        return msvcrt.open_osfhandle(
            handle, (os.O_WRONLY if create_new else os.O_RDONLY) | os.O_BINARY
        )
    except BaseException:
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))
        if create_new:
            path.unlink()
        raise


def _private_temp(root):
    if os.name == "nt":
        path = root / (".capture-" + uuid4().hex)
        return _windows_fd(path, create_new=True), path
    fd, name = tempfile.mkstemp(dir=root, prefix=".capture-")
    return fd, Path(name)


class _DigestReader(io.RawIOBase):
    """Hash bytes consumed by the header/record check without owning the file."""

    def __init__(self, stream):
        self.stream = stream
        self.digest = hashlib.sha256()
        self.length = 0

    def readable(self):
        return True

    def readinto(self, buffer):
        count = self.stream.readinto(buffer)
        self.digest.update(memoryview(buffer)[:count])
        self.length += count
        return count


def _csv_tokens(text):
    while block := text.read(_CHUNK):
        for match in _CSV_TOKENS.finditer(block):
            yield match[0]
    yield None  # EOF also terminates a final record without a line ending.


def _csv_header_width(columns):
    names = set(columns)
    if not names.issuperset(CANONICAL_COLUMNS):
        raise MissingColumnsError("Snapshot CSV is missing required header columns.")
    if len(names) != len(columns) or any(not name.strip() or "\0" in name for name in names):
        raise MalformedSourceError("Invalid snapshot CSV header.")
    return len(columns)


def _csv_count(stream, encoding):
    # Check structure during the pre-hash read: pandas can truncate a surplus
    # field at a chunk boundary. This costs CPU but adds no file pass. Retain
    # header names only; data fields can span any number of read buffers.
    codec = codecs.lookup(encoding).name
    width = count = header_chars = 0
    columns = []
    name_parts = []
    fields = 1
    state = "start"
    blank = True
    with io.TextIOWrapper(
        stream, encoding="utf-8-sig" if codec == "utf-8" else codec, newline=""
    ) as text:
        for token in _csv_tokens(text):
            if token is None:
                if state == "quoted":
                    raise MalformedSourceError("Unterminated quoted snapshot CSV field.")
                token = "\n"
            if not width and (state == "quoted" or token not in ("\r", "\n")):
                header_chars += len(token)
                if header_chars > _MAX_CSV_HEADER_CHARS:
                    raise MalformedSourceError("Snapshot CSV header exceeds the schema size limit.")

            if token == '"':
                if state == "start":
                    state = "quoted"
                    continue
                if state == "quoted":
                    state = "after_quote"
                    continue
                if state == "after_quote":
                    state = "quoted"  # A doubled quote contributes one literal quote.
                # A quote inside an unquoted field is literal as well.
            elif state != "quoted":
                if token in (",", "\r", "\n"):
                    if not width:
                        columns.append("".join(name_parts))
                        name_parts.clear()
                    state = "start"
                    if token == ",":
                        fields += 1
                        blank = False
                        continue
                    if not blank:
                        if not width:
                            width = _csv_header_width(columns)
                        elif fields != width:
                            raise MalformedSourceError("Inconsistent snapshot CSV record width.")
                        else:
                            count += 1
                    columns.clear()
                    fields, blank, header_chars = 1, True, 0
                    continue
                if state == "after_quote":
                    raise MalformedSourceError("Unexpected text after a quoted snapshot CSV field.")
                state = "plain"

            if not width:
                name_parts.append(token)
            if blank and not token.isspace():
                blank = False
    if not width:
        raise MalformedSourceError("Snapshot CSV has no header.")
    return count


def _check_identity(stream, snapshot, csv_encoding=None):
    stream.seek(0)
    if os.fstat(stream.fileno()).st_size != snapshot.size_bytes:
        raise SnapshotError("Snapshot digest or length mismatch.")
    count = None
    with _DigestReader(stream) as raw, io.BufferedReader(raw, _CHUNK) as reader:
        if csv_encoding is None:
            while reader.read(_CHUNK):
                pass
        else:
            try:
                count = _csv_count(reader, csv_encoding)
            except (UnicodeError, LookupError):
                raise MalformedSourceError("Invalid snapshot CSV syntax or encoding.") from None
    if raw.digest.hexdigest() != snapshot.sha256 or raw.length != snapshot.size_bytes:
        raise SnapshotError("Snapshot digest or length mismatch.")
    stream.seek(0)
    return count


def _open_object(root, publisher, snapshot):
    path = root / snapshot.sha256
    info = _object_custody(path, publisher)
    if os.name == "nt":
        fd = _windows_fd(path, create_new=False)
    else:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    stream = os.fdopen(fd, "rb")
    if not info.st_ino or not os.path.samestat(info, os.fstat(stream.fileno())):
        stream.close()
        raise SnapshotError("Snapshot object changed during opening.")
    return stream


def capture_snapshot(path: Path | str) -> SourceSnapshot:
    """Copy once and publish without replacement. POSIX also syncs the directory.

    A directory-sync failure leaves the object available for a verified retry.
    Windows retains file fsync and atomic publication without a directory-sync guarantee.
    """
    try:
        root, publisher = _root()
        path = Path(path).absolute()
        info = _check_path(path)
        if not stat.S_ISREG(info.st_mode):
            raise SnapshotError("Snapshot input must be a regular file.")
        with path.open("rb") as source:
            if not os.path.samestat(info, os.fstat(source.fileno())):
                raise SnapshotError("Snapshot input changed during opening.")
            fd, temporary = _private_temp(root)
            try:
                digest = hashlib.sha256()
                length = 0
                with os.fdopen(fd, "wb") as target:
                    while block := source.read(_CHUNK):
                        target.write(block)
                        digest.update(block)
                        length += len(block)
                    target.flush()
                    if os.name == "posix":
                        os.fchmod(fd, 0o440)
                    os.fsync(fd)
                after = os.fstat(source.fileno())
                if not (
                    length == info.st_size == after.st_size
                    and info.st_mtime_ns == after.st_mtime_ns
                ):
                    raise SnapshotError("Snapshot input changed during capture.")
                snapshot = SourceSnapshot(digest.hexdigest(), length)
                _object_custody(temporary, publisher)
                try:
                    os.link(temporary, root / snapshot.sha256)
                except FileExistsError:
                    with _open_object(root, publisher, snapshot) as existing:
                        _check_identity(existing, snapshot)
            finally:
                temporary.unlink()
            if os.name == "posix":
                _sync_published_directory(root)
            return snapshot
    except (OSError, NotImplementedError):
        raise SnapshotError("Snapshot capture or hard-link publication failed.") from None


def _sync_published_directory(root):
    # Include both the published link and temporary unlink, also on verified reuse.
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        raise SnapshotError(
            "Snapshot directory sync failed; publication durability is unconfirmed."
        ) from None


@dataclass
class _SheetVisits:
    entered: list[str] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    counts: list[tuple[int, int]] = field(default_factory=list)


class SnapshotTraversal:
    """Single-use iterator. Successful verification is observable after context exit."""

    def __init__(self, batches, visits, sheets, expected_rows):
        self._visits = visits
        self._sheets = sheets
        self._verified = False
        self._exhausted = False
        self._iterator = self._consume(batches, expected_rows)

    def __iter__(self) -> Iterator[SourceBatch]:
        return self._iterator

    @property
    def selected_sheets(self) -> tuple[str, ...]:
        return self._sheets

    @property
    def entered_sheets(self) -> tuple[str, ...]:
        return tuple(self._visits.entered)

    @property
    def completed_sheets(self) -> tuple[str, ...]:
        return tuple(self._visits.completed)

    @property
    def sheet_counts(self) -> tuple[tuple[int, int], ...]:
        """Rows and emitted batches at each successfully closed worksheet."""
        return tuple(self._visits.counts)

    @property
    def verified_exhausted(self) -> bool:
        return self._verified

    def _consume(self, batches, expected_rows):
        count = 0
        try:
            for batch in batches:
                count += len(batch)
                yield batch
        except MissingColumnsError:
            raise MissingColumnsError("Selected source is missing required columns.") from None
        except Exception:
            # Parser diagnostics may contain original cell values, paths or XML.
            raise MalformedSourceError(
                "Invalid snapshot data encountered during parsing."
            ) from None
        if (
            self.selected_sheets != self.entered_sheets
            or self.selected_sheets != self.completed_sheets
        ):
            raise SnapshotError("Selected sheet traversal is incomplete.")
        if expected_rows is not None and expected_rows != count:
            raise SnapshotError("CSV parser and snapshot record counts disagree.")
        self._exhausted = True


def _validate_options(filename, suffix, batch_size):
    if (
        not isinstance(filename, str)
        or not filename
        or re.search(r'[\\/:*?"<>|\x00-\x1f\x7f]', filename)
        or PureWindowsPath(filename).is_reserved()
    ):
        raise SnapshotError("Expected a safe logical source filename.")
    if Path(filename).suffix.lower() != suffix:
        raise SnapshotError("Unsupported snapshot source format.")
    if type(batch_size) is not int or batch_size <= 0:
        raise SnapshotError("Expected a positive batch size.")


@contextmanager
def _traverse(snapshot, source, csv_encoding=None):
    if type(snapshot) is not SourceSnapshot:
        raise SnapshotError("Expected a snapshot identity.")
    try:
        root, publisher = _root()
        stream = _open_object(root, publisher, snapshot)
    except OSError:
        raise SnapshotError("Snapshot object is unavailable.") from None
    with stream:
        started = perf_counter()
        expected_rows = _check_identity(stream, snapshot, csv_encoding)
        precheck_seconds = perf_counter() - started
        visits = _SheetVisits()
        with ExitStack() as parsers:
            parsers.callback(source.__exit__, None, None, None)
            try:
                source._open(stream)
            except Exception:
                raise MalformedSourceError("Could not open selected snapshot source.") from None
            if isinstance(source, ExcelSource):
                sheets = tuple(source.sheet_names)
                if not sheets or len(set(sheets)) != len(sheets):
                    raise MalformedSourceError("Invalid worksheet scope.")
                batches = source._extract(visits)
            else:
                sheets = ()
                batches = source.extract()
            parsers.enter_context(closing(batches))
            traversal = SnapshotTraversal(batches, visits, sheets, expected_rows)
            traversal.precheck_seconds = precheck_seconds
            traversal.postcheck_seconds = None
            parsers.enter_context(closing(iter(traversal)))
            yield traversal
        if traversal._exhausted:
            if stream.closed:
                raise SnapshotError("Parser closed the snapshot verification handle.")
            started = perf_counter()
            _check_identity(stream, snapshot)
            try:
                _check_path(root)
                current = _object_custody(root / snapshot.sha256, publisher)
            except OSError:
                raise SnapshotError("Snapshot object is unavailable.") from None
            if not os.path.samestat(current, os.fstat(stream.fileno())):
                raise SnapshotError("Snapshot object changed during traversal.")
            traversal._verified = True
            traversal.postcheck_seconds = perf_counter() - started


def open_snapshot_csv(
    snapshot: SourceSnapshot, *, filename: str, batch_size: int = 50_000, encoding: str = "utf-8"
):
    """Traverse a plain, rectangular CSV using its non-sensitive logical basename.

    Bound screening admits up to 8 Mi decoded characters in the logical header.
    Data field and physical line lengths have no scanner limit. The pandas
    reader retains its original inference options.
    """
    _validate_options(filename, ".csv", batch_size)
    if not isinstance(encoding, str) or not encoding:
        raise SnapshotError("Invalid CSV encoding.")
    return _traverse(snapshot, CsvSource(filename, batch_size, encoding), encoding)


def open_snapshot_excel(
    snapshot: SourceSnapshot,
    *,
    filename: str,
    sheet_names: Sequence[str] | None = None,
    batch_size: int = 50_000,
):
    """Stream selected worksheets in order, using stored formula results (data_only=True)."""
    _validate_options(filename, ".xlsx", batch_size)
    if sheet_names is not None:
        if isinstance(sheet_names, (str, bytes)) or not isinstance(sheet_names, Sequence):
            raise MalformedSourceError("Expected an ordered sheet selection.")
        sheet_names = tuple(sheet_names)
        if (
            not sheet_names
            or any(not isinstance(s, str) or not s for s in sheet_names)
            or len(set(sheet_names)) != len(sheet_names)
        ):
            raise MalformedSourceError("Invalid worksheet selection.")
    return _traverse(snapshot, ExcelSource(filename, sheet_names, batch_size))
