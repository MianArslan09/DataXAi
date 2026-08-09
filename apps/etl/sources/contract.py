"""
The canonical contract every Source implementation must satisfy, so the
Transform layer (Volume 4.3, not built yet) can consume CSV, Excel, or
PostgreSQL data identically without knowing which one it got.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator, Protocol, runtime_checkable

import pandas as pd

# Raw column names as they exist in the actual UCI Online Retail II
# dataset - confirmed by direct inspection in Volume 4.1 (both real
# sheets share this exact header), not assumed or invented. Every
# Source must produce a DataFrame with exactly these columns before
# yielding a SourceBatch. Renaming to warehouse field names (invoice_no,
# stock_code, ...) is Volume 4.3's job, not this layer's.
CANONICAL_COLUMNS = (
    "Invoice",
    "StockCode",
    "Description",
    "Quantity",
    "InvoiceDate",
    "Price",
    "Customer ID",
    "Country",
)


@dataclass(frozen=True)
class SourceBatch:
    """One chunk of extracted data plus metadata about where it came
    from. `data` is guaranteed (checked in __post_init__) to have
    exactly CANONICAL_COLUMNS - Transform can rely on this without
    checking which Source produced it."""

    data: pd.DataFrame
    source_name: str
    batch_number: int
    extracted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self):
        from etl.exceptions import MissingColumnsError

        missing = set(CANONICAL_COLUMNS) - set(self.data.columns)
        if missing:
            raise MissingColumnsError(
                f"{self.source_name} batch {self.batch_number} is missing "
                f"columns: {sorted(missing)}"
            )

    def __len__(self) -> int:
        return len(self.data)


@runtime_checkable
class Source(Protocol):
    """Every source is a context manager (connections/file handles are
    always cleaned up, even on error) whose extract() yields SourceBatch
    objects. Implementations: CsvSource, ExcelSource, PostgresSource."""

    def __enter__(self) -> "Source": ...

    def __exit__(self, exc_type, exc_val, exc_tb) -> None: ...

    def extract(self) -> Iterator[SourceBatch]: ...
