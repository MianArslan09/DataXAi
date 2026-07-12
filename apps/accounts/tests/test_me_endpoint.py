import pytest
from rest_framework.test import APIClient

from accounts.models import User


@pytest.mark.django_db
class TestMeEndpoint:
    def test_me_requires_authentication(self):
        client = APIClient()
        response = client.get("/api/auth/me/")
        assert response.status_code == 401

    def test_me_returns_current_user_with_role(self):
        User.objects.create_user(
            username="analyst1",
            password="a-strong-test-password-5",
            role=User.Role.BUSINESS_ANALYST,
        )
        client = APIClient()
        token_response = client.post(
            "/api/auth/token/",
            {"username": "analyst1", "password": "a-strong-test-password-5"},
            format="json",
        )
        access_token = token_response.data["access"]
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        response = client.get("/api/auth/me/")
        assert response.status_code == 200
        assert response.data["username"] == "analyst1"
        assert response.data["role"] == "business_analyst"
