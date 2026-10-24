"""Who is looking at a group chat right now (so a push is not sent to someone who is already reading it)."""

from __future__ import annotations

from django.core.cache import cache

PRESENCE_TTL_SECONDS = 120


def _key(group_id: int, user_id: int) -> str:
    return f"chat:presence:{group_id}:{user_id}"


def mark_present(group_id: int, user_id: int) -> None:
    try:
        cache.set(_key(group_id, user_id), True, timeout=PRESENCE_TTL_SECONDS)
    except Exception:  # presence is a courtesy; the chat must work without the cache
        pass


def mark_absent(group_id: int, user_id: int) -> None:
    try:
        cache.delete(_key(group_id, user_id))
    except Exception:
        pass


def is_present(group_id: int, user_id: int) -> bool:
    try:
        return bool(cache.get(_key(group_id, user_id)))
    except Exception:
        return False
