from __future__ import annotations

from typing import cast

from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import RefreshToken

from .devices import DEVICE_HASH_CLAIM, DeviceBindingRefused, DeviceBindingService, invalidated_session_error
from .models import User

SESSION_VERSION_CLAIM = "sv"


class SessionVersionRefreshToken(RefreshToken):
    """Refresh token carrying the user session version at issue time."""

    @classmethod
    def for_user(cls, user, device_hash: str | None = None):
        token = super().for_user(user)
        token[SESSION_VERSION_CLAIM] = cast(User, user).session_version
        if device_hash:
            # Copied into the access token too: the device the session belongs to (see apps.accounts.devices).
            token[DEVICE_HASH_CLAIM] = device_hash
        return token


def _token_session_version(token) -> int:
    try:
        return int(token.get(SESSION_VERSION_CLAIM, 1))
    except (TypeError, ValueError) as exc:
        raise AuthenticationFailed("Invalid token session state.") from exc


def _device_event_for_dead_token(raw: str):
    from rest_framework_simplejwt.exceptions import TokenBackendError
    from rest_framework_simplejwt.state import token_backend

    try:
        payload = token_backend.decode(str(raw), verify=True)  # type: ignore[arg-type]  # accepts the raw JWT string
    except TokenBackendError:
        return None
    if payload.get("token_type") != "refresh":
        return None
    return invalidated_session_error(payload.get(api_settings.USER_ID_CLAIM), payload.get(DEVICE_HASH_CLAIM))


class SessionVersionJWTAuthentication(JWTAuthentication):
    """Reject access tokens invalidated by password, role, or status changes (and, once device binding is enforced,
    student tokens presented from a device other than the bound one)."""

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is not None:
            user, validated_token = result
            DeviceBindingService.check_request(user, validated_token, request)
        return result

    def get_user(self, validated_token):
        user = cast(User, super().get_user(validated_token))
        if _token_session_version(validated_token) != user.session_version:
            device_error = invalidated_session_error(user.pk, validated_token.get(DEVICE_HASH_CLAIM))
            if device_error is not None:
                raise device_error
            raise AuthenticationFailed("Token has been revoked.", code="token_revoked")
        return user


class SessionVersionTokenRefreshSerializer(TokenRefreshSerializer):
    """Reject old refresh tokens before SimpleJWT rotates them; students must refresh from their bound device.

    ``DeviceBindingRefused`` is raised for a device mismatch; the view renders it (see ``TokenRefreshView``).
    """

    def validate(self, attrs):
        try:
            refresh = self.token_class(attrs["refresh"])
        except TokenError as exc:
            # Blacklisted by a device switch/reset? Decode WITH signature and expiry checks (never verify=False) so the
            # specific code is only given for a genuine token of a device that is no longer bound.
            device_error = _device_event_for_dead_token(attrs["refresh"])
            if device_error is not None:
                raise device_error from exc
            raise InvalidToken(exc.args[0]) from exc
        user_id = refresh.get(api_settings.USER_ID_CLAIM)
        user = User.objects.filter(pk=user_id, is_active=True, is_deleted=False).first()
        if user is None or _token_session_version(refresh) != user.session_version:
            device_error = invalidated_session_error(user_id, refresh.get(DEVICE_HASH_CLAIM))
            if device_error is not None:
                raise device_error
            raise AuthenticationFailed("Token has been revoked.", code="token_revoked")
        request = self.context.get("request")
        if request is not None:
            device_hash = DeviceBindingService.gate_refresh(user, request, refresh.get(DEVICE_HASH_CLAIM))
            if device_hash:
                refresh[DEVICE_HASH_CLAIM] = device_hash
        data = {"access": str(refresh.access_token)}
        if api_settings.ROTATE_REFRESH_TOKENS:
            if api_settings.BLACKLIST_AFTER_ROTATION:
                refresh.blacklist()
            refresh.set_jti()
            refresh.set_exp()
            refresh.set_iat()
            refresh.outstand()
            data["refresh"] = str(refresh)
        return data


__all__ = ["DeviceBindingRefused", "SessionVersionJWTAuthentication", "SessionVersionTokenRefreshSerializer"]
