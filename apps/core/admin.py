from django.contrib import admin

from .models import AuditLogEntry


@admin.register(AuditLogEntry)
class AuditLogEntryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "pipeline_run",
        "row_reference",
        "fault_type",
        "strategy_applied",
        "occurred_at",
    )
    list_filter = ("fault_type", "strategy_applied")
    search_fields = ("row_reference", "column_name", "narrative")
    readonly_fields = ("occurred_at",)
    date_hierarchy = "occurred_at"
