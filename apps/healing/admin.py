from django.contrib import admin

from .models import QuarantineRecord


@admin.register(QuarantineRecord)
class QuarantineRecordAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "pipeline_run",
        "row_reference",
        "fault_code",
        "status",
        "resolved_by",
        "resolved_at",
    )
    list_filter = ("status", "fault_code")
    search_fields = ("row_reference", "column_name")
    raw_id_fields = ("pipeline_run", "resolved_by")
    date_hierarchy = "created_at"
