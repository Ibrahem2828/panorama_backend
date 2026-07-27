from __future__ import annotations

from django.conf import settings


def get_client_ip(request) -> str | None:
    """Resolve client IP only through the configured number of trusted proxies.

    The direct peer is always included at the end of the chain. Setting
    TRUSTED_PROXY_COUNT=0 ignores X-Forwarded-For entirely.
    """
    if request is None:
        return None
    remote = str(request.META.get("REMOTE_ADDR", "") or "").strip()
    trusted = max(0, int(getattr(settings, "TRUSTED_PROXY_COUNT", 0)))
    if trusted == 0:
        return remote or None
    forwarded = [part.strip() for part in str(request.META.get("HTTP_X_FORWARDED_FOR", "")).split(",") if part.strip()]
    chain = forwarded + ([remote] if remote else [])
    if len(chain) <= trusted:
        return remote or None
    return chain[-(trusted + 1)]
