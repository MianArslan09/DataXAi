import pytest

from accounts.models import User
from core.choices import FaultType
from core.exceptions import QuarantineResolutionError
from etl.models import PipelineRun
from healing.models import QuarantineRecord


@pytest.mark.django_db
class TestQuarantineRecord:
    def _make_record(self):
        run = PipelineRun.objects.create()
        return QuarantineRecord.objects.create(
            pipeline_run=run,
            row_reference="536365:85123A",
            fault_code=FaultType.REFERENTIAL_INTEGRITY,
            column_name="customer_id",
            raw_value="99999999",
            recommended_action="Verify customer_id against source CRM; likely a typo.",
        )

    def test_default_status_is_pending(self):
        record = self._make_record()
        assert record.status == QuarantineRecord.Status.PENDING

    def test_resolve_sets_all_four_fields_together(self):
        record = self._make_record()
        admin = User.objects.create_user(
            username="admin1", password="a-strong-test-password-3", role=User.Role.ADMIN
        )
        record.resolve(
            user=admin, status=QuarantineRecord.Status.APPROVED, notes="Confirmed valid customer."
        )
        record.refresh_from_db()
        assert record.status == QuarantineRecord.Status.APPROVED
        assert record.resolved_by == admin
        assert record.resolved_at is not None
        assert record.resolution_notes == "Confirmed valid customer."

    def test_cannot_resolve_twice(self):
        record = self._make_record()
        admin = User.objects.create_user(
            username="admin2", password="a-strong-test-password-4", role=User.Role.ADMIN
        )
        record.resolve(user=admin, status=QuarantineRecord.Status.APPROVED)
        with pytest.raises(QuarantineResolutionError):
            record.resolve(user=admin, status=QuarantineRecord.Status.REJECTED)
