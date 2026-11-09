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
    readonly_fields = ("created_at", "updated_at", "attempt")
    date_hierarchy = "created_at"

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.attempt_id is not None:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.attempt_id is not None:
            return False
        return super().has_delete_permission(request, obj)
