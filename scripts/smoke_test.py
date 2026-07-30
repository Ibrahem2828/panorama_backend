#!/usr/bin/env python3
"""Post-deploy public and authenticated smoke checks without fixture creation."""

from __future__ import annotations

import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BASE_URL = os.environ.get("SMOKE_BASE_URL", "").rstrip("/")
TOKEN = os.environ.get("SMOKE_BEARER_TOKEN", "")


def request(path: str, *, authenticated: bool = False) -> dict:
    headers = {"Accept": "application/json"}
    if authenticated:
        if not TOKEN:
            raise RuntimeError("SMOKE_BEARER_TOKEN is required for authenticated smoke checks")
        headers["Authorization"] = f"Bearer {TOKEN}"
    try:
        with urlopen(Request(f"{BASE_URL}{path}", headers=headers), timeout=10) as response:
            body = json.loads(response.read())
            if response.status != 200:
                raise RuntimeError(f"{path}: expected 200, got {response.status}")
            return body
    except HTTPError as exc:
        raise RuntimeError(f"{path}: expected 200, got {exc.code}") from exc
    except (URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{path}: unavailable or invalid JSON") from exc


def main() -> int:
    if not BASE_URL.startswith("https://"):
        raise RuntimeError("SMOKE_BASE_URL must be an HTTPS deployment URL")
    for endpoint, code in (("/api/v1/health/live/", "LIVE"), ("/api/v1/health/ready/", "READY"), ("/api/v1/health/startup/", "STARTUP_READY")):
        if request(endpoint).get("code") != code:
            raise RuntimeError(f"{endpoint}: unexpected success payload")
    current_user = request("/api/v1/auth/me/", authenticated=True)
    if not current_user.get("success"):
        raise RuntimeError("/api/v1/auth/me/: authentication smoke check failed")
    print("Smoke checks passed: live, ready, startup, and authenticated current-user route.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as exc:
        print(f"Smoke checks failed: {exc}", file=sys.stderr)
        sys.exit(1)
