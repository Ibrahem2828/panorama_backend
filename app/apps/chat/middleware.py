from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from rest_framework.exceptions import PermissionDenied
from rest_framework_simplejwt.authentication import JWTAuthentication

from .services import ChatWebSocketTokenService


@database_sync_to_async
def get_user_for_token(token: str):
    if not token:
        return AnonymousUser()
    try:
        authenticator = JWTAuthentication()
        validated = authenticator.get_validated_token(token)
        return authenticator.get_user(validated)
    except Exception:
        return AnonymousUser()


@database_sync_to_async
def get_user_for_ws_token(token: str):
    from apps.accounts.models import User

    try:
        payload = ChatWebSocketTokenService.validate_token(token)
        user = User.objects.filter(pk=payload["user_id"], is_active=True).first()
        if user is None:
            return AnonymousUser(), None, "inactive_user"
        return user, payload, ""
    except PermissionDenied as exc:
        return AnonymousUser(), None, str(exc.detail)
    except Exception:
        return AnonymousUser(), None, "invalid_token"


class JWTAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        query = parse_qs(scope.get("query_string", b"").decode())
        token = query.get("token", [""])[0]
        user, payload, error = await get_user_for_ws_token(token)
        if user.is_authenticated:
            scope["user"] = user
            scope["ws_token"] = payload
            scope["ws_token_error"] = ""
            return await self.app(scope, receive, send)

        if settings.ALLOW_WEBSOCKET_ACCESS_TOKEN_AUTH:
            scope["user"] = await get_user_for_token(token)
            scope["ws_token"] = None
            scope["ws_token_error"] = ""
            return await self.app(scope, receive, send)

        scope["user"] = AnonymousUser()
        scope["ws_token"] = None
        scope["ws_token_error"] = error or "invalid_token"
        return await self.app(scope, receive, send)
