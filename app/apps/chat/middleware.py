from __future__ import annotations

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.authentication import JWTAuthentication


@database_sync_to_async
def get_user_for_token(token: str):
    if not token:
        return AnonymousUser()
    try:
        authenticator = JWTAuthentication()
        validated = authenticator.get_validated_token(token)
        user = authenticator.get_user(validated)
        return user if user.is_active else AnonymousUser()
    except Exception:
        return AnonymousUser()


def _header_token(scope) -> str:
    headers = {key.lower(): value for key, value in scope.get("headers", [])}
    authorization = headers.get(b"authorization", b"").decode("utf-8", errors="ignore")
    if authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return ""


class JWTAuthMiddleware:
    """Authenticate WebSockets via Authorization header; query token is legacy fallback."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        token = _header_token(scope)
        if not token:
            query = parse_qs(scope.get("query_string", b"").decode("utf-8", errors="ignore"))
            token = query.get("token", [""])[0]
        scope["user"] = await get_user_for_token(token)
        return await self.app(scope, receive, send)
