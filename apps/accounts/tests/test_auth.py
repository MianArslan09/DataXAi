import pytest
from rest_framework.test import APIClient

from accounts.models import User


@pytest.mark.django_db
def test_can_obtain_jwt_token_pair():
    User.objects.create_user(username="engineer1", password="a-strong-test-password-1")
    client = APIClient()
    response = client.post(
        "/api/auth/token/",
        {"username": "engineer1", "password": "a-strong-test-password-1"},
        format="json",
    )
    assert response.status_code == 200
    assert "access" in response.data
    assert "refresh" in response.data
