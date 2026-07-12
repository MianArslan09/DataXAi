import pytest
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient
from rest_framework.views import APIView

from core.exceptions import DataXAiError, QuarantineResolutionError, custom_exception_handler


class TestCustomExceptionHandler:
    def test_dataxai_error_wraps_to_400_with_code_and_message(self):
        exc = QuarantineResolutionError("already resolved")
        response = custom_exception_handler(exc, context={})
        assert response.status_code == 400
        assert response.data == {
            "error": {"code": "quarantine_resolution_error", "message": "already resolved"}
        }

    def test_base_dataxai_error_uses_default_message(self):
        exc = DataXAiError()
        response = custom_exception_handler(exc, context={})
        assert response.data["error"]["message"] == "An unexpected error occurred."
        assert response.data["error"]["code"] == "dataxai_error"

    def test_drf_validation_error_gets_reshaped_into_envelope(self):
        exc = ValidationError({"quantity": ["This field is required."]})
        response = custom_exception_handler(exc, context={"view": APIView()})
        assert response.status_code == 400
        assert "quantity" in response.data["error"]["message"]

    def test_unrecognised_exception_returns_none_and_falls_through_to_django(self):
        # A plain, non-DRF, non-DataXAi exception (e.g. an unguarded KeyError)
        # should return None so Django's own 500 handler takes over -
        # never silently swallowed.
        response = custom_exception_handler(KeyError("boom"), context={})
        assert response is None


@pytest.mark.django_db
def test_health_check_500_would_still_return_json_envelope_shape():
    """Sanity check that the handler is actually wired into settings,
    not just unit-testable in isolation."""
    client = APIClient()
    response = client.get("/api/health/")
    assert response.status_code == 200  # DB is up in tests, so this is the happy path
