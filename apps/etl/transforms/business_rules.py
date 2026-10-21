"""Approved Volume 4.3.3 cancellation-marker enrichment, after TypeConverter.

The marker records only the strict uppercase-C invoice convention. It does
not classify returns/refunds, repair values, filter rows, or normalize dates.
"""

from decimal import Decimal

import pandas as pd

from etl.exceptions import AmbiguousColumnsError, ETLError, MissingColumnsError
from etl.transforms.contract import TARGET_COLUMNS, TransformedBatch


class BusinessRules:
    """Append nullable is_cancellation while preserving the typed input batch."""

    def apply(self, batch: TransformedBatch) -> TransformedBatch:
        """Return a new batch; reject schema collisions and non-string identifiers.

        Null/empty/whitespace-only invoices produce pd.NA. Other strings are
        matched exactly as supplied, independently of quantity and price.
        """
        frame = batch.data
        context = f"{batch.source_name} batch {batch.batch_number}"

        # The frozen batch contains a mutable DataFrame: check at point of use.
        missing = [column for column in TARGET_COLUMNS if column not in frame.columns]
        if missing:
            raise MissingColumnsError(
                f"{context} is missing transformed columns: {sorted(missing)}"
            )
        invoice_positions = [
            position for position, column in enumerate(frame.columns) if column == "invoice_no"
        ]
        if len(invoice_positions) > 1:
            raise AmbiguousColumnsError(
                f"{context} contains duplicate required column 'invoice_no' "
                f"at zero-based positions {invoice_positions}"
            )
        if "is_cancellation" in frame.columns:
            raise AmbiguousColumnsError(
                f"{context} already contains 'is_cancellation'; enrichment cannot overwrite it"
            )

        markers = []
        for row_index, invoice in frame["invoice_no"].items():
            if isinstance(invoice, str):
                markers.append(
                    pd.NA if not invoice or invoice.isspace() else invoice.startswith("C")
                )
            elif (
                pd.api.types.is_scalar(invoice)
                # Decimal sNaN is invalid, not a null; pandas may raise on it.
                and not (isinstance(invoice, Decimal) and invoice.is_snan())
                and pd.isna(invoice)
            ):
                markers.append(pd.NA)
            else:
                # A typed-input boundary error, not an attempt at type conversion.
                bad_repr = repr(invoice)
                if len(bad_repr) > 200:
                    bad_repr = bad_repr[:197] + "..."
                raise ETLError(
                    f"{context}, column 'invoice_no', row index {row_index}: "
                    f"BusinessRules requires a string or null after TypeConverter; "
                    f"got {bad_repr} ({type(invoice).__name__})"
                )

        # Assign positionally so duplicate indexes cannot realign the markers.
        enriched = frame.copy(deep=True)
        enriched["is_cancellation"] = pd.array(markers, dtype="boolean")
        return TransformedBatch(
            data=enriched,
            source_name=batch.source_name,
            batch_number=batch.batch_number,
            extracted_at=batch.extracted_at,
        )
