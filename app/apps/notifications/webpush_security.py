"""Endpoint and key validation for browser Web Push subscriptions.

A subscription endpoint is an attacker-controllable URL that the server later POSTs to, so it is treated as an SSRF
vector: only ``https`` on the default port, no credentials, no IP literals (which also rules out loopback, private
and link-local ranges), no local-only names, and the host must belong to a push service allowlist that is a setting
(``WEB_PUSH_ALLOWED_HOST_SUFFIXES``) so a new browser vendor is a configuration change.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import re
from urllib.parse import urlsplit

from django.conf import settings

ENDPOINT_MAX_LENGTH = 1024
#: A P-256 public key is 65 bytes (uncompressed point); the auth secret is 16 bytes.
P256DH_RAW_LENGTH = 65
AUTH_RAW_LENGTH = 16
_BASE64URL = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")
_LOCAL_SUFFIXES = (".local", ".localhost", ".internal", ".intranet", ".lan", ".home", ".corp")


class InvalidWebPushInput(ValueError):
    """The endpoint is not an acceptable push service URL (the message is safe to show to API clients)."""


def allowed_host_suffixes() -> tuple[str, ...]:
    return tuple(getattr(settings, "WEB_PUSH_ALLOWED_HOST_SUFFIXES", ()))


def _host_allowed(host: str, suffixes: tuple[str, ...]) -> bool:
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in suffixes)


def validate_endpoint(value: str) -> str:
    """Return the normalised endpoint or raise ``InvalidWebPushInput``."""

    endpoint = str(value or "").strip()
    if not endpoint or len(endpoint) > ENDPOINT_MAX_LENGTH:
        raise InvalidWebPushInput(f"The push endpoint must be 1-{ENDPOINT_MAX_LENGTH} characters long.")
    try:
        parts = urlsplit(endpoint)
        port = parts.port
    except ValueError as exc:
        raise InvalidWebPushInput("The push endpoint is not a valid URL.") from exc
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme != "https" or not host:
        raise InvalidWebPushInput("The push endpoint must be an absolute https URL.")
    if parts.username or parts.password or port not in (None, 443) or parts.fragment:
        raise InvalidWebPushInput("The push endpoint must not contain credentials, a custom port or a fragment.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise InvalidWebPushInput("The push endpoint must use a host name, not an IP address.")
    if "." not in host or host == "localhost" or host.endswith(_LOCAL_SUFFIXES):
        raise InvalidWebPushInput("The push endpoint host is not a public push service.")
    suffixes = allowed_host_suffixes()
    if suffixes and not _host_allowed(host, suffixes):
        raise InvalidWebPushInput("The push endpoint is not served by a supported push service.")
    return endpoint


def validate_key(value: str, *, raw_length: int, field: str) -> str:
    """Validate a base64url subscription key of an exact decoded length and return it without padding."""

    text = str(value or "").strip()
    if not text or len(text) > 255 or not _BASE64URL.match(text):
        raise InvalidWebPushInput(f"{field} must be a base64url string.")
    unpadded = text.rstrip("=")
    try:
        raw = base64.urlsafe_b64decode(unpadded + "=" * (-len(unpadded) % 4))
    except (binascii.Error, ValueError) as exc:
        raise InvalidWebPushInput(f"{field} must be a base64url string.") from exc
    if len(raw) != raw_length:
        raise InvalidWebPushInput(f"{field} has an invalid length.")
    return unpadded
