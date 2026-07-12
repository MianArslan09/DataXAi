import pytest

from accounts.models import User
from etl.models import PipelineRun


@pytest.mark.django_db
class TestPipelineRun:
    def test_default_status_and_json_fields(self):
        run = PipelineRun.objects.create()
        assert run.status == PipelineRun.Status.PENDING
        assert run.fault_counts == {}
        assert run.cdqi_scores == {}
        assert run.triggered_by is None

    def test_duration_seconds_is_none_until_finished(self):
        run = PipelineRun.objects.create()
        assert run.duration_seconds is None

    def test_duration_seconds_computed_once_finished(self):
        from django.utils import timezone

        started = timezone.now()
        finished = started + timezone.timedelta(seconds=42)
        run = PipelineRun.objects.create(started_at=started, finished_at=finished)
        assert run.duration_seconds == pytest.approx(42.0, abs=1.0)

    def test_triggered_by_survives_user_deletion_as_null(self):
        user = User.objects.create_user(username="engineer2", password="a-strong-test-password-2")
        run = PipelineRun.objects.create(triggered_by=user)
        user.delete()
        run.refresh_from_db()
        assert (
            run.triggered_by is None
        )  # on_delete=SET_NULL, not CASCADE - the run record must survive
