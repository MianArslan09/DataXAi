"""
RBAC on top of accounts.User.role (Volume 2 added the field; this is the
part that actually enforces it - Volume 1, Section 1.6's "full RBAC
enforcement is Volume 3's job" note).

Design decision: Admin is treated as a superset of every other role,
matching the <<extend>> relationships around the Admin actor in the
proposal's use case diagram (Figure 3) - an Admin can always do what a
Data Engineer or Business Analyst can, plus admin-only actions. Roles are
NOT stacked the other way: a Data Engineer cannot do Business-Analyst-only
things or vice versa unless explicitly allowed.
"""

from rest_framework.permissions import BasePermission


class HasRole(BasePermission):
    """
    Base class - subclasses set `allowed_roles`. Not used directly on a
    view; use one of the concrete subclasses below (or compose your own
    the same way for a new role combination).
    """

    allowed_roles: tuple = ()
    message = "Your role does not have access to this action."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if not (user and user.is_authenticated):
            return False
        if user.role == user.Role.ADMIN:
            return True
        return user.role in self.allowed_roles


class IsDataEngineer(HasRole):
    """Data Engineer only (plus Admin, via the superset rule above).
    Use for: trigger pipeline run, approve/reject DLQ, configure params."""

    def __init__(self):
        from accounts.models import User

        self.allowed_roles = (User.Role.DATA_ENGINEER,)


class IsBusinessAnalystOrAbove(HasRole):
    """Business Analyst or Data Engineer (plus Admin). Use for: read-only
    dashboard views (CDQI, forecasts, CLV, quarantine review, chatbot)."""

    def __init__(self):
        from accounts.models import User

        self.allowed_roles = (User.Role.DATA_ENGINEER, User.Role.BUSINESS_ANALYST)


class IsAdminRole(HasRole):
    """Admin only - no superset exception applies here since Admin *is*
    the role being checked. Use for: user management, purge quarantine,
    Isolation Forest configuration, system logs."""

    def __init__(self):
        self.allowed_roles = ()
