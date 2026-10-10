"""
Transform layer contract (Volume 4.3.1).

Volume 4.2's Source layer guarantees a SourceBatch whose DataFrame carries
the raw UCI column names (CANONICAL_COLUMNS) and nothing about types or
values. This module defines what the Transform layer hands to the next
stage: a TransformedBatch whose DataFrame carries the *warehouse field
names* below, plus the source batch's metadata, unchanged.

Stage guarantee at 4.3.1: column NAMES only. Values, dtypes, row order,
row count and the DataFrame index are exactly what the Source produced.
Type coercion, date normalization and business rules are separate,
later subsections; each strengthens the guarantee of the data carried in
a TransformedBatch without changing this class's shape.

Nothing in this module knows or cares whether the data came from CSV,
Excel or PostgreSQL.
"""

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Mapping, Protocol, runtime_checkable

import pandas as pd

from etl.exceptions import MissingColumnsError
from etl.sources.contract import CANONICAL_COLUMNS, SourceBatch

# Raw UCI column -> warehouse field name. Every target is the exact field
# name on the warehouse model that eventually owns it:
#   invoice_no, quantity, unit_price, invoice_date -> warehouse.OrderLine
#   stock_code, description                        -> warehouse.Product
#   external_customer_id, country                  -> warehouse.Customer
# (OrderLine.product / OrderLine.customer are resolved from stock_code /
# external_customer_id at load time, Volume 4.4; they are not columns.)
ALIAS_MAP: Mapping[str, str] = MappingProxyType(
    {
        "Invoice": "invoice_no",
        "StockCode": "stock_code",
        "Description": "description",
        "Quantity": "quantity",
        "InvoiceDate": "invoice_date",
        "Price": "unit_price",
        "Customer ID": "external_customer_id",
        "Country": "country",
    }
)

# Target column names in the same order as CANONICAL_COLUMNS.
TARGET_COLUMNS: tuple[str, ...] = tuple(ALIAS_MAP[c] for c in CANONICAL_COLUMNS)


@dataclass(frozen=True)
class TransformedBatch:
    """One batch after the Transform layer: a DataFrame plus the metadata
    of the SourceBatch it came from. `data` is guaranteed (checked in
    __post_init__) to contain every column in TARGET_COLUMNS. Presence,
    not exact equality, is checked so later subsections can add derived
    columns without changing this invariant."""

    data: pd.DataFrame
    source_name: str
    batch_number: int
    extracted_at: datetime

    def __post_init__(self):
        missing = [c for c in TARGET_COLUMNS if c not in self.data.columns]
        if missing:
            raise MissingColumnsError(
                f"{self.source_name} batch {self.batch_number} is missing "
                f"transformed columns: {sorted(missing)}"
            )

    def __len__(self) -> int:
        return len(self.data)


@runtime_checkable
class Transform(Protocol):
    """The Transform layer's contract: SourceBatch in, TransformedBatch
    out. Downstream stages depend on this protocol, never on a concrete
    transform."""

    def transform(self, batch: SourceBatch) -> TransformedBatch: ...
