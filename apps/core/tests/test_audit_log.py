import pytest

from core.choices import FaultType
from core.models import AuditLogEntry
from etl.models import PipelineRun


@pytest.mark.django_db
class TestAuditLogEntry:
    def test_create_minimal_entry(self):
        run = PipelineRun.objects.create()
        entry = AuditLogEntry.objects.create(pipeline_run=run)
        assert entry.pk is not None
        assert entry.fault_type == ""
        assert entry.occurred_at is not None

    def test_create_full_entry(self):
        run = PipelineRun.objects.create()
        entry = AuditLogEntry.objects.create(
            pipeline_run=run,
            row_reference="536365:85123A",
            column_name="unit_price",
            fault_type=FaultType.TYPE_ERROR,
            strategy_applied="type_coercion",
            old_value="2.55usd",
            new_value="2.55",
            narrative=(
                "Row 536365 had a non-numeric price ('2.55usd'); coerced to 2.55 "
                "by stripping the unit suffix."
            ),
        )
        assert entry.pipeline_run == run
        assert run.audit_entries.count() == 1

    def test_deleting_pipeline_run_cascades_to_audit_entries(self):
        run = PipelineRun.objects.create()
        AuditLogEntry.objects.create(pipeline_run=run)
        run.delete()
        assert AuditLogEntry.objects.count() == 0
