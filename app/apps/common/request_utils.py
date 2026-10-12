from __future__ import annotations

import hmac
import ipaddress

from django.conf import settings

BFF_SECRET_HEADER = "X-Panorama-BFF-Secret"
BFF_CLIENT_IP_HEADER = "X-Panorama-Client-IP"
_BFF_SECRET_META = "HTTP_X_PANORAMA_BFF_SECRET"
_BFF_CLIENT_IP_META = "HTTP_X_PANORAMA_CLIENT_IP"


def _trusted_bff_client_ip(request) -> str | None:
    """Return the end-user IP asserted by our own BFF, only when it proves it knows the shared secret.

    Traefik overwrites ``X-Forwarded-For`` with the BFF's address, so without this every web/dashboard user
    would share one address (and one throttle bucket). The BFF authenticates itself with
    ``X-Panorama-BFF-Secret`` (constant-time compare) and forwards the real address in ``X-Panorama-Client-IP``.
    Browsers can never obtain the secret and the headers are not in the CORS allow-list. The secret is never
    logged or returned. Anything invalid is ignored so the caller falls back to the proxy-chain logic.
    """
    secret = str(getattr(settings, "BFF_SHARED_SECRET", "") or "")
    if not secret:
        return None
    supplied = str(request.META.get(_BFF_SECRET_META, "") or "")
    if not supplied or not hmac.compare_digest(supplied.encode("utf-8"), secret.encode("utf-8")):
        return None
    candidate = str(request.META.get(_BFF_CLIENT_IP_META, "") or "").strip()
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def get_client_ip(request) -> str | None:
    """Resolve client IP only through the configured number of trusted proxies.

    The direct peer is always included at the end of the chain. Setting
    TRUSTED_PROXY_COUNT=0 ignores X-Forwarded-For entirely. When ``BFF_SHARED_SECRET`` is configured, a request
    carrying that secret may assert the real end-user IP via ``X-Panorama-Client-IP``.
    """
    if request is None:
        return None
    meta = getattr(request, "META", None)
    if meta is None:
        return None
    bff_ip = _trusted_bff_client_ip(request)
    if bff_ip:
        return bff_ip
    remote = str(meta.get("REMOTE_ADDR", "") or "").strip()
    trusted = max(0, int(getattr(settings, "TRUSTED_PROXY_COUNT", 0)))
    if trusted == 0:
        return remote or None
    forwarded = [part.strip() for part in str(meta.get("HTTP_X_FORWARDED_FOR", "")).split(",") if part.strip()]
    chain = forwarded + ([remote] if remote else [])
    if len(chain) <= trusted:
        return remote or None
    return chain[-(trusted + 1)]
