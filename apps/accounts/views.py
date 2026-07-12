from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import UserSerializer


class MeView(APIView):
    """GET /api/auth/me/ - who am I and what role am I. The dashboard
    (Volume 11) uses this once at login to decide which of the five
    views to show/hide, rather than re-deriving role from the JWT payload
    on the frontend."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)
