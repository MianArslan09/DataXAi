from unittest.mock import MagicMock

import pytest

from accounts.models import User
from core.permissions import IsAdminRole, IsBusinessAnalystOrAbove, IsDataEngineer


def _request_for(user):
    request = MagicMock()
    request.user = user
    return request


@pytest.mark.django_db
class TestRolePermissions:
    def test_unauthenticated_user_is_denied(self):
        request = _request_for(MagicMock(is_authenticated=False))
        assert IsDataEngineer().has_permission(request, None) is False

    def test_data_engineer_permission_allows_data_engineer(self):
        user = User.objects.create_user(username="de1", password="x", role=User.Role.DATA_ENGINEER)
        assert IsDataEngineer().has_permission(_request_for(user), None) is True

    def test_data_engineer_permission_denies_business_analyst(self):
        user = User.objects.create_user(
            username="ba1", password="x", role=User.Role.BUSINESS_ANALYST
        )
        assert IsDataEngineer().has_permission(_request_for(user), None) is False

    def test_admin_is_a_superset_of_every_role(self):
        admin = User.objects.create_user(username="admin3", password="x", role=User.Role.ADMIN)
        for permission_class in (IsDataEngineer, IsBusinessAnalystOrAbove, IsAdminRole):
            assert permission_class().has_permission(_request_for(admin), None) is True

    def test_business_analyst_or_above_allows_both_non_admin_roles(self):
        engineer = User.objects.create_user(
            username="de2", password="x", role=User.Role.DATA_ENGINEER
        )
        analyst = User.objects.create_user(
            username="ba2", password="x", role=User.Role.BUSINESS_ANALYST
        )
        assert IsBusinessAnalystOrAbove().has_permission(_request_for(engineer), None) is True
        assert IsBusinessAnalystOrAbove().has_permission(_request_for(analyst), None) is True

    def test_admin_only_denies_non_admins(self):
        analyst = User.objects.create_user(
            username="ba3", password="x", role=User.Role.BUSINESS_ANALYST
        )
        assert IsAdminRole().has_permission(_request_for(analyst), None) is False
