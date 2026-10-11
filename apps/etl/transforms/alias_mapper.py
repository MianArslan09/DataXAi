"""
AliasMapper (Volume 4.3.1): renames raw UCI columns to warehouse field
names via ALIAS_MAP. That is its entire job.

It does NOT coerce types, parse dates, filter or reorder rows, handle
nulls, derive is_cancellation, or touch the DataFrame index. Row count,
row order, index, dtypes and values pass through exactly as the Source
produced them.
"""

from etl.exceptions import AmbiguousColumnsError, MissingColumnsError
from etl.sources.contract import CANONICAL_COLUMNS, SourceBatch
from etl.transforms.contract import ALIAS_MAP, TransformedBatch


class AliasMapper:
    """Implements the Transform protocol for the alias-mapping stage."""

    def transform(self, batch: SourceBatch) -> TransformedBatch:
        # SourceBatch validates columns at construction, but its DataFrame
        # is mutable, so re-check at the point of use.
        missing = [c for c in CANONICAL_COLUMNS if c not in batch.data.columns]
        if missing:
            raise MissingColumnsError(
                f"{batch.source_name} batch {batch.batch_number} is missing "
                f"columns: {sorted(missing)}"
            )

        positions_by_column = {column: [] for column in CANONICAL_COLUMNS}
        for position, column in enumerate(batch.data.columns):
            if column in positions_by_column:
                positions_by_column[column].append(position)

        duplicates = {
            column: positions
            for column, positions in positions_by_column.items()
            if len(positions) > 1
        }
        if duplicates:
            details = "; ".join(
                f"{column!r} at zero-based positions {positions}"
                for column, positions in duplicates.items()
            )
            raise AmbiguousColumnsError(
                f"{batch.source_name} batch {batch.batch_number} contains "
                f"duplicate required source columns: {details}"
            )

        # Select exactly the 8 raw columns in canonical order (any extra
        # columns a CSV/Excel file carried are dropped), then rename. Both
        # steps return a new DataFrame; batch.data is never mutated.
        mapped = batch.data.loc[:, list(CANONICAL_COLUMNS)].rename(columns=dict(ALIAS_MAP))

        return TransformedBatch(
            data=mapped,
            source_name=batch.source_name,
            batch_number=batch.batch_number,
            extracted_at=batch.extracted_at,
        )
