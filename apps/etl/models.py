"""
M1 - ETL Engine. `PipelineRun` is the one concrete model this app owns:
a row per batch execution, which is what the Pipeline Health dashboard
view (M7) reads from and what M4's healing/quarantine records and M6's
audit log entries point back to via `pipeline_run` FKs.

The actual extract/transform code (Volume 4) is stateless and doesn't
live in models.py - this file only holds what needs to persist.
"""

from django.db import models

from core.models import TimeStampedModel


class PipelineRun(TimeStampedModel):
    """
    One row per batch run. `fault_counts` and `cdqi_scores` are JSONB so
    Volume 5 (validation) and Volume 6 (healing) can add new keys - new
    fault types or new CDQI dimensions - without a schema migration.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    records_ingested = models.PositiveIntegerField(default=0)
    records_healed = models.PositiveIntegerField(default=0)
    records_quarantined = models.PositiveIntegerField(default=0)

    fault_counts = models.JSONField(
        default=dict, blank=True, help_text="Per fault-type counts, e.g. {'missing_value': 12}"
    )
    cdqi_scores = models.JSONField(
        default=dict,
        blank=True,
        help_text="ISO 25012 dimensions (completeness/accuracy/consistency/timeliness/"
        "uniqueness) plus the composite score, before and after healing.",
    )

    triggered_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="triggered_pipeline_runs",
        help_text="Null for Celery Beat-scheduled runs.",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"PipelineRun({self.pk}, {self.status})"

    @property
    def duration_seconds(self):
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None
