from __future__ import annotations

from typing import cast

from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import RefreshToken

from .models import User

SESSION_VERSION_CLAIM = "sv"


class SessionVersionRefreshToken(RefreshToken):
    """Refresh token carrying the user session version at issue time."""

    @classmethod
    def for_user(cls, user):
        token = super().for_user(user)
        token[SESSION_VERSION_CLAIM] = cast(User, user).session_version
        return token


def _token_session_version(token) -> int:
    try:
        return int(token.get(SESSION_VERSION_CLAIM, 1))
    except (TypeError, ValueError) as exc:
        raise AuthenticationFailed("Invalid token session state.") from exc


class SessionVersionJWTAuthentication(JWTAuthentication):
    """Reject access tokens invalidated by password, role, or status changes."""

    def get_user(self, validated_token):
        user = cast(User, super().get_user(validated_token))
        if _token_session_version(validated_token) != user.session_version:
            raise AuthenticationFailed("Token has been revoked.", code="token_revoked")
        return user


class SessionVersionTokenRefreshSerializer(TokenRefreshSerializer):
    """Reject old refresh tokens before SimpleJWT rotates them."""

    def validate(self, attrs):
        try:
            refresh = self.token_class(attrs["refresh"])
        except TokenError as exc:
            raise InvalidToken(exc.args[0]) from exc
        user_id = refresh.get(api_settings.USER_ID_CLAIM)
        user = User.objects.filter(pk=user_id, is_active=True, is_deleted=False).first()
        if user is None or _token_session_version(refresh) != user.session_version:
            raise AuthenticationFailed("Token has been revoked.", code="token_revoked")
        return super().validate(attrs)
