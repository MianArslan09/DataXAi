from django.contrib import admin

from .models import PipelineRun


@admin.register(PipelineRun)
class PipelineRunAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "status",
        "started_at",
        "finished_at",
        "records_ingested",
        "records_healed",
        "records_quarantined",
    )
    list_filter = ("status",)
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "created_at"
