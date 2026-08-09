"""
CSV Source - reads local CSV files in chunks via pandas.read_csv so a
large file never needs to be fully materialized in memory at once.
"""

import logging
from pathlib import Path
from typing import Iterator, Union

import pandas as pd

from etl.exceptions import MalformedSourceError, MissingColumnsError, SourceNotFoundError
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
        if not self.path.exists():
            raise SourceNotFoundError(f"CSV file not found: {self.path}")
        self._reader = pd.read_csv(self.path, chunksize=self.batch_size, encoding=self.encoding)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._reader is not None:
            self._reader.close()
            self._reader = None

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
                    raise MissingColumnsError(f"{self.path} is missing columns: {sorted(missing)}")
                logger.info(
                    "csv_source.batch",
                    extra={"path": str(self.path), "batch": batch_number, "rows": len(chunk)},
                )
                yield SourceBatch(
                    data=chunk, source_name=f"csv:{self.path.name}", batch_number=batch_number
                )
                batch_number += 1
        except pd.errors.ParserError as exc:
            raise MalformedSourceError(f"Could not parse CSV {self.path}: {exc}") from exc
        except UnicodeDecodeError as exc:
            raise MalformedSourceError(
                f"Could not decode CSV {self.path} as {self.encoding}: {exc}"
            ) from exc
