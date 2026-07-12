"""
The Clean Warehouse (DS2 in the proposal's Figure 2 DFD). Only M4 (healing)
writes here, and only after a record has passed validation/detection/repair
- nothing upstream of the Auto-Heal module ever touches these tables.

Grain note: UCI Online Retail II is order-*line* grained (one row per
invoice + stock code), not one row per invoice - OrderLine reflects that,
not "Order".
"""

from django.db import models

from core.models import TimeStampedModel


class Customer(TimeStampedModel):
    """
    One row per source CustomerID. `external_customer_id` (not `id`) is
    what the source system/ETL refers to - UCI Online Retail II ships some
    blank CustomerIDs (guest-like transactions), so this is nullable and
    OrderLine.customer is nullable too.
    """

    external_customer_id = models.CharField(max_length=32, unique=True, db_index=True)
    country = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["external_customer_id"]

    def __str__(self):
        return f"Customer({self.external_customer_id})"


class Product(TimeStampedModel):
    """One row per StockCode (SKU). Description can legitimately vary
    slightly across source rows for the same StockCode in the raw UCI
    data (free-text entry) - the ETL layer (Volume 4) is responsible for
    picking a canonical description; this table stores the result."""

    stock_code = models.CharField(max_length=32, unique=True, db_index=True)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["stock_code"]

    def __str__(self):
        return f"Product({self.stock_code})"


class OrderLine(TimeStampedModel):
    """
    One healed, validated row of transaction data. `is_cancellation` is
    set by the ETL layer from the source InvoiceNo convention (a leading
    "C") rather than inferred here, so this table stays a plain data
    store with no business logic in it.

    `quantity` is intentionally NOT constrained to be non-negative -
    negative quantities are legitimate returns/cancellations in this
    dataset, not a data quality fault (see Volume 1, Section 1.6 "RFM"
    note: return rate is one of the six CLV features and depends on
    exactly these negative-quantity rows existing).
    """

    invoice_no = models.CharField(max_length=32, db_index=True)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="order_lines")
    customer = models.ForeignKey(
        Customer, on_delete=models.PROTECT, related_name="order_lines", null=True, blank=True
    )
    quantity = models.IntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=4)
    invoice_date = models.DateTimeField(db_index=True)
    is_cancellation = models.BooleanField(default=False)
    source_batch = models.ForeignKey(
        "etl.PipelineRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_lines",
        help_text=(
            "Which pipeline run loaded this row - full raw-to-dashboard " "traceability (NFR-07)."
        ),
    )

    class Meta:
        ordering = ["-invoice_date"]
        indexes = [
            models.Index(
                fields=["product", "invoice_date"]
            ),  # Prophet's SKU-level daily aggregation
            models.Index(fields=["customer", "invoice_date"]),  # RFM feature computation
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(unit_price__gte=0),
                name="orderline_unit_price_non_negative",
            ),
        ]

    def __str__(self):
        return f"OrderLine(invoice={self.invoice_no}, sku={self.product_id})"
