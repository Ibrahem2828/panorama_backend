import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _isolate_cache():
    """Throttle counters, stats caches and feature-flag caches live in the (shared, in-process) cache."""

    cache.clear()
    yield
    cache.clear()
