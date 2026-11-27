"""
CSV Source - reads local CSV files in chunks via pandas.read_csv so a
large file never needs to be fully materialized in memory at once.
"""

import logging
from pathlib import Path
from typing import Iterator, Union

import pandas as pd

from etl._coordinator_diagnostics import safe_log
from etl.exceptions import MalformedSourceError, MissingColumnsError, SourceNotFoundError
from etl.sources._cleanup import close_resources
from etl.sources.contract import CANONICAL_COLUMNS, SourceBatch

logger = logging.getLogger(__name__)


class CsvSource:
    """Usage: `with CsvSource(path) as source: for batch in source.extract(): ...`"""

    def __init__(self, path: Union[str, Path], batch_size: int = 50_000, encoding: str = "utf-8"):
        self.path = Path(path)
        self.batch_size = batch_size
        self.encoding = encoding
        self._reader = None

    def __enter__(self) -> "CsvSource":
        return self._open(self.path)

    def _open(self, stream_or_path) -> "CsvSource":
        try:
            self._reader = pd.read_csv(
                stream_or_path, chunksize=self.batch_size, encoding=self.encoding
            )
        except FileNotFoundError as exc:
            raise SourceNotFoundError() from exc
        except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
            raise MalformedSourceError("Could not parse CSV source.") from exc
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        reader, self._reader = self._reader, None
        if reader is not None:
            close_resources(reader.close, primary_error=exc_val)

    def extract(self) -> Iterator[SourceBatch]:
        if self._reader is None:
            raise RuntimeError(
                "CsvSource must be used as a context manager: `with CsvSource(...) as s:`"
            )
        batch_number = 0
        try:
            for chunk in self._reader:
                missing = set(CANONICAL_COLUMNS) - set(chunk.columns)
                if missing:
                    raise MissingColumnsError(f"CSV source is missing columns: {sorted(missing)}")
                safe_log(
                    logger,
                    logging.INFO,
                    "csv_source.batch",
                    extra={"batch": batch_number, "rows": len(chunk)},
                )
                yield SourceBatch(
                    data=chunk, source_name=f"csv:{self.path.name}", batch_number=batch_number
                )
                batch_number += 1
        except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
            raise MalformedSourceError("Could not parse CSV source.") from exc
