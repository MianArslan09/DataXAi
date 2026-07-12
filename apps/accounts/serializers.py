from rest_framework import serializers

from .models import User


class UserSerializer(serializers.ModelSerializer):
    """Read-only representation of the current user - used by MeView.
    User creation/editing is an Admin-panel concern (Volume 15), not
    exposed generally, since this is an internal tool with a fixed set
    of roles rather than public self-registration."""

    class Meta:
        model = User
        fields = ["id", "username", "email", "role", "is_active", "date_joined"]
        read_only_fields = fields
