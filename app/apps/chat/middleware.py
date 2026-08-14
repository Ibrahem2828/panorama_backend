from __future__ import annotations

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

from apps.accounts.authentication import SessionVersionJWTAuthentication

from .services import ChatHandshakeTicketService


def _header_token(scope) -> str:
    headers = {key.lower(): value for key, value in scope.get("headers", [])}
    authorization = headers.get(b"authorization", b"").decode("utf-8", errors="ignore")
    if authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return ""


def _single_query_value(scope, name: str) -> str:
    query = parse_qs(scope.get("query_string", b"").decode("utf-8", errors="ignore"), keep_blank_values=True)
    values = query.get(name, [])
    return values[0] if len(values) == 1 else ""


@database_sync_to_async
def get_user_for_handshake_ticket(ticket: str, group_id: int):
    claims = ChatHandshakeTicketService.consume(ticket, group_id=group_id)
    if claims is None:
        return AnonymousUser(), None
    user = ChatHandshakeTicketService.authenticate_claims(claims, group_id=group_id)
    return (user, claims["session_version"]) if user is not None else (AnonymousUser(), None)


@database_sync_to_async
def get_user_for_legacy_token(token: str):
    """Temporary migration path, guarded by an explicit disabled-by-default flag."""

    if not token:
        return AnonymousUser(), None
    try:
        # Retain SimpleJWT signature/expiry validation while applying the same
        # session-version check as REST authentication.
        authenticator = SessionVersionJWTAuthentication()
        validated = JWTAuthentication().get_validated_token(token.encode("utf-8"))
        user = authenticator.get_user(validated)
        return user if user.is_active else AnonymousUser(), getattr(user, "session_version", None)
    except (AuthenticationFailed, InvalidToken, TokenError):
        # Authentication failures must close the socket without exposing token details.
        return AnonymousUser(), None


class ChatTicketAuthMiddleware:
    """Authenticate chat sockets with one-time cache-backed tickets.

    The middleware is placed *inside* the URL router so the matched `group_id`
    is available before consuming a group-bound ticket.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        scope = dict(scope)
        group_id = scope.get("url_route", {}).get("kwargs", {}).get("group_id")
        try:
            group_id = int(group_id)
        except (TypeError, ValueError):
            scope["user"] = AnonymousUser()
            return await self.app(scope, receive, send)

        ticket = _single_query_value(scope, "ticket")
        if ticket:
            user, session_version = await get_user_for_handshake_ticket(ticket, group_id)
            scope["ws_auth_mode"] = "ticket"
        elif settings.WEBSOCKET_LEGACY_JWT_QUERY_AUTH_ENABLED:
            # Deprecated migration support only. The production default is off.
            token = _header_token(scope) or _single_query_value(scope, "token")
            user, session_version = await get_user_for_legacy_token(token)
            scope["ws_auth_mode"] = "legacy_jwt"
        else:
            user, session_version = AnonymousUser(), None
            scope["ws_auth_mode"] = "none"
        scope["user"] = user
        scope["ws_session_version"] = session_version
        return await self.app(scope, receive, send)


# Compatibility alias for integrations that imported the previous class directly.
JWTAuthMiddleware = ChatTicketAuthMiddleware
