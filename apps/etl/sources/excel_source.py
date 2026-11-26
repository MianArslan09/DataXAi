"""
Excel/XLSX Source - the real UCI Online Retail II dataset's actual
format. Streams rows via openpyxl's read_only mode rather than
pandas.read_excel, which loads a whole sheet into memory before
returning anything (measured at ~75s / ~249MB for the real 1.07M-row
workbook during Volume 4.1 inspection - not acceptable as the default
read strategy on the proposal's 8GB-RAM target hardware).

Defaults to reading every sheet in the workbook, in order, because the
real UCI file needs both of its sheets ("Year 2009-2010" and
"Year 2010-2011") ingested - restricting to one sheet would silently
drop half the real dataset.
"""

import logging
from pathlib import Path
from typing import Iterator, Optional, Sequence, Union
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

import openpyxl
import pandas as pd
from openpyxl.reader.excel import SUPPORTED_FORMATS
from openpyxl.utils.exceptions import InvalidFileException

from etl.exceptions import MalformedSourceError, MissingColumnsError, SourceNotFoundError
from etl.sources._cleanup import cleanup_on_exit, close_resources
from etl.sources.contract import CANONICAL_COLUMNS, SourceBatch

logger = logging.getLogger(__name__)

_PARSER_ERRORS = (InvalidFileException, BadZipFile, ParseError)
if openpyxl.LXML:
    from lxml.etree import XMLSyntaxError

    _PARSER_ERRORS += (XMLSyntaxError,)


class ExcelSource:
    """Usage: `with ExcelSource(path) as source: for batch in source.extract(): ...`
    Pass `sheet_names=[...]` to restrict to specific sheets instead of
    reading the whole workbook."""

    def __init__(
        self,
        path: Union[str, Path],
        sheet_names: Optional[Sequence[str]] = None,
        batch_size: int = 50_000,
    ):
        self.path = Path(path)
        self._requested_sheets = list(sheet_names) if sheet_names else None
        self.sheet_names: list = []
        self.batch_size = batch_size
        self._workbook = None
        self._stream = None

    def __enter__(self) -> "ExcelSource":
        # Own the input handle even when openpyxl fails before returning a workbook.
        try:
            self._stream = self.path.open("rb")
        except FileNotFoundError as exc:
            raise SourceNotFoundError() from exc
        try:
            if self.path.suffix.lower() not in SUPPORTED_FORMATS:
                raise MalformedSourceError("Unsupported workbook format.")
            return self._open(self._stream)
        except BaseException as exc:
            self.__exit__(type(exc), exc, exc.__traceback__)
            raise

    def _open(self, stream_or_path) -> "ExcelSource":
        try:
            self._workbook = openpyxl.load_workbook(stream_or_path, read_only=True, data_only=True)
            available = self._workbook.sheetnames
            requested = self._requested_sheets or available
            if set(requested) - set(available):
                raise MalformedSourceError("Selected worksheet is unavailable.")
            self.sheet_names = requested
        except BaseException as exc:
            self.__exit__(type(exc), exc, exc.__traceback__)
            if isinstance(exc, _PARSER_ERRORS):
                raise MalformedSourceError("Could not parse Excel source.") from exc
            raise
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        workbook, self._workbook = self._workbook, None
        stream, self._stream = self._stream, None
        callbacks = [resource.close for resource in (workbook, stream) if resource is not None]
        close_resources(*callbacks, primary_error=exc_val)

    def extract(self) -> Iterator[SourceBatch]:
        yield from self._extract()

    def _extract(self, visits=None) -> Iterator[SourceBatch]:
        try:
            yield from self._extract_batches(visits)
        except _PARSER_ERRORS as exc:
            raise MalformedSourceError("Could not parse Excel source.") from exc

    def _extract_batches(self, visits=None) -> Iterator[SourceBatch]:
        if self._workbook is None:
            raise RuntimeError(
                "ExcelSource must be used as a context manager: `with ExcelSource(...) as s:`"
            )
        batch_number = 0
        for sheet_name in self.sheet_names:
            first_batch = batch_number
            sheet_rows = 0
            worksheet = self._workbook[sheet_name]
            if visits is None:
                rows_iter = worksheet.iter_rows(values_only=True)
            else:
                visits.entered.append(sheet_name)
                rows_iter = self._bound_rows(worksheet)
            with cleanup_on_exit(rows_iter.close):
                try:
                    header = list(next(rows_iter))
                except StopIteration:
                    if visits is not None:
                        raise MalformedSourceError("Selected worksheet has no header.") from None
                    logger.warning("excel_source.empty_sheet")
                    continue

                missing = set(CANONICAL_COLUMNS) - set(header)
                if visits is not None and (
                    any(
                        type(name) is not str or not name.strip() or "\0" in name for name in header
                    )
                    or len(set(header)) != len(header)
                ):
                    raise MalformedSourceError("Invalid selected worksheet header.")
                if missing:
                    raise MissingColumnsError(f"Excel source is missing columns: {sorted(missing)}")

                buffer = []
                for row in rows_iter:
                    buffer.append(row)
                    if len(buffer) >= self.batch_size:
                        if visits is not None:
                            sheet_rows += len(buffer)
                        yield self._make_batch(header, buffer, sheet_name, batch_number)
                        batch_number += 1
                        buffer = []
                if buffer:
                    if visits is not None:
                        sheet_rows += len(buffer)
                    yield self._make_batch(header, buffer, sheet_name, batch_number)
                    batch_number += 1
            if visits is not None:
                visits.completed.append(sheet_name)
                visits.counts.append((sheet_rows, batch_number - first_batch))

    @staticmethod
    def _bound_rows(worksheet):
        # The normal read-only iterator stops at declared dimensions, even when
        # the XML contains more rows/cells. Read to XML EOF in this same pass.
        if not hasattr(worksheet, "reset_dimensions"):
            raise MalformedSourceError("Unsupported worksheet type.")
        height, width = worksheet.max_row, worksheet.max_column
        if (
            (height is None) != (width is None)
            or (width or 0) > 16_384
            or (height or 0) > 1_048_576
        ):
            raise MalformedSourceError("Unsupported worksheet dimensions.")
        worksheet.reset_dimensions()
        row_number = 0
        rows = worksheet.iter_rows(min_row=1, values_only=True)
        with cleanup_on_exit(rows.close):
            for row_number, row in enumerate(rows, start=1):
                if height is not None and (row_number > height or len(row) > width):
                    raise MalformedSourceError("Worksheet data exceeds declared dimensions.")
                # Preserve the normal adapter's padding for sparse worksheet rows.
                if width is not None and len(row) < width:
                    row += (None,) * (width - len(row))
                yield row
        if height is not None and row_number != height:
            raise MalformedSourceError("Worksheet data disagrees with declared dimensions.")

    def _make_batch(self, header, rows, sheet_name: str, batch_number: int) -> SourceBatch:
        df = pd.DataFrame(rows, columns=header)
        logger.info(
            "excel_source.batch",
            extra={
                "batch": batch_number,
                "rows": len(df),
            },
        )
        return SourceBatch(
            data=df, source_name=f"xlsx:{self.path.name}:{sheet_name}", batch_number=batch_number
        )
