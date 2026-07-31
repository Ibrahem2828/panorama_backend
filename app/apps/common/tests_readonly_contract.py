from __future__ import annotations

import pytest
from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework import status
from rest_framework.test import APIClient

from .viewsets import StandardReadOnlyModelViewSet


def _router_patterns(patterns):
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from _router_patterns(pattern.url_patterns)
        elif isinstance(pattern, URLPattern):
            actions = getattr(pattern.callback, "actions", None)
            viewset = getattr(pattern.callback, "cls", None)
            if actions is not None and viewset is not None:
                yield pattern, viewset, actions


def test_read_only_viewset_router_contract_exposes_get_only():
    """Test the resolver map, not only the base-class name or OpenAPI output."""

    read_only_routes = []
    for pattern, viewset, actions in _router_patterns(get_resolver().url_patterns):
        if issubclass(viewset, StandardReadOnlyModelViewSet):
            read_only_routes.append((str(pattern.pattern), viewset.__name__, actions))
            assert set(actions) <= {"get", "head"}, (pattern.pattern, viewset.__name__, actions)

    assert read_only_routes


@pytest.mark.django_db
def test_public_read_only_resource_rejects_write_verbs_at_runtime():
    client = APIClient()

    assert (
        client.post("/api/v1/universities/", {"name": "not allowed"}, format="json").status_code
        == status.HTTP_405_METHOD_NOT_ALLOWED
    )
    assert (
        client.put("/api/v1/universities/999/", {"name": "not allowed"}, format="json").status_code
        == status.HTTP_405_METHOD_NOT_ALLOWED
    )
    assert (
        client.patch("/api/v1/universities/999/", {"name": "not allowed"}, format="json").status_code
        == status.HTTP_405_METHOD_NOT_ALLOWED
    )
    assert client.delete("/api/v1/universities/999/").status_code == status.HTTP_405_METHOD_NOT_ALLOWED
