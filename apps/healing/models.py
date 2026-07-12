"""
M4 - Auto-Heal Module and Quarantine DLQ. `QuarantineRecord` is the DLQ
table from Section 3.2.4 / Figure 1: every fault the strategy registry
(Volume 6) can't safely auto-resolve lands here with a fault code, a
timestamp, and a recommended manual action - never silently guessed at.
"""

from django.db import models

from core.choices import FaultType
from core.models import TimeStampedModel


class QuarantineRecord(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending Review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        EDITED_AND_APPROVED = "edited_and_approved", "Edited and Approved"

    pipeline_run = models.ForeignKey(
        "etl.PipelineRun", on_delete=models.CASCADE, related_name="quarantine_records"
    )
    row_reference = models.CharField(
        max_length=128,
        help_text="Source-row identifier (e.g. invoice_no:stock_code) - not an FK, "
        "since a quarantined row failed validation and may not be a real "
        "warehouse record yet.",
    )
    fault_code = models.CharField(max_length=32, choices=FaultType.choices)
    column_name = models.CharField(max_length=64, blank=True)
    raw_value = models.TextField(
        blank=True, help_text="Original value, stored as text since source type varies."
    )
    recommended_action = models.TextField()

    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING)
    resolved_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_quarantine_records",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "fault_code"]),
        ]

    def __str__(self):
        return f"QuarantineRecord({self.row_reference}, {self.fault_code}, {self.status})"

    def resolve(self, *, user, status, notes=""):
        """
        Single entry point for approve/reject/edit-and-approve (the DLQ
        actions from the use case diagram) so every resolution path sets
        the same four fields consistently - no view or task should set
        `status`/`resolved_by`/`resolved_at` directly.
        """
        from django.utils import timezone

        if self.status != self.Status.PENDING:
            from core.exceptions import QuarantineResolutionError

            raise QuarantineResolutionError(
                f"QuarantineRecord {self.pk} is already {self.status}; cannot resolve twice."
            )
        self.status = status
        self.resolved_by = user
        self.resolved_at = timezone.now()
        self.resolution_notes = notes
        self.save(
            update_fields=["status", "resolved_by", "resolved_at", "resolution_notes", "updated_at"]
        )
        return self
