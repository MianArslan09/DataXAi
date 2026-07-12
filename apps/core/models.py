from django.db import models

from core.choices import FaultType


class TimeStampedModel(models.Model):
    """Abstract base for every domain model going forward (Volumes 3-9)."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AuditLogEntry(TimeStampedModel):
    """
    DB-queryable mirror of what Loguru also writes to the structured JSON
    log file (Volume 1, Section 1.1: "the actual product is the audit
    trail"). Loguru's file-based log is the durable, append-only source of
    truth for compliance/traceability; this table exists purely so the
    M7 "View Repair Audit Trail" dashboard view can filter/sort/paginate
    without parsing log files on every request.

    Every field except `pipeline_run` and `occurred_at` is blank-able:
    not every entry is about a single cell repair (a batch-level note has
    no column_name; a quarantine event has no strategy_applied).
    """

    pipeline_run = models.ForeignKey(
        "etl.PipelineRun", on_delete=models.CASCADE, related_name="audit_entries"
    )
    row_reference = models.CharField(max_length=128, blank=True)
    column_name = models.CharField(max_length=64, blank=True)
    fault_type = models.CharField(max_length=32, choices=FaultType.choices, blank=True)
    strategy_applied = models.CharField(max_length=64, blank=True)
    old_value = models.TextField(blank=True)
    new_value = models.TextField(blank=True)
    narrative = models.TextField(
        blank=True,
        help_text="Plain-language explanation from M6 (Jinja2 template, "
        "extended via Groq for multi-fault rows).",
    )
    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["pipeline_run", "occurred_at"]),
        ]
        verbose_name_plural = "audit log entries"

    def __str__(self):
        return f"AuditLogEntry(run={self.pipeline_run_id}, row={self.row_reference})"
