from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from apps.common.protected_media import ProtectedMediaService


class ProtectedMediaView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(tags=["Protected Media"], responses={200: bytes})
    def get(self, request, token: str):
        return ProtectedMediaService.serve(token)
