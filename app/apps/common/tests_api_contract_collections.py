import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from django.urls import Resolver404, resolve

from apps.common.responses import error_response, success_response


ROOT_DIR = Path(__file__).resolve().parents[3]
API_COLLECTIONS = (
    ROOT_DIR / "docs" / "api" / "mobile_api_collection.json",
    ROOT_DIR / "docs" / "api" / "dashboard_api_collection.json",
)
REQUIRED_ENDPOINT_FIELDS = {"name", "method", "path", "auth_required"}
VALID_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}
TEMPLATE_VARIABLE_RE = re.compile(r"\{\{[a-zA-Z_][a-zA-Z0-9_]*\}\}")


def _load_collection(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_endpoints(collection: dict):
    for group in collection.get("groups", []):
        for endpoint in group.get("endpoints", []):
            yield group["name"], endpoint


def _split_methods(methods: str) -> list[str]:
    return [method.strip().upper() for method in methods.split("|") if method.strip()]


def _path_without_query(path: str) -> str:
    return urlsplit(path).path


def _representative_path(path: str) -> str:
    return TEMPLATE_VARIABLE_RE.sub("1", _path_without_query(path))


def _candidate_paths(endpoint: dict, method: str) -> list[str]:
    documented_path = _path_without_query(endpoint["path"])
    representative = _representative_path(endpoint["path"])
    candidates = [representative]

    # Dashboard CRUD collection entries document one row path for all methods.
    # DRF creates POST routes on the list URL, so validate that compatible route too.
    if method == "POST" and re.search(r"/\{\{id\}\}/$", documented_path):
        candidates.append(re.sub(r"/1/$", "/", representative))

    return candidates


def _resolved_route_supports_method(path: str, method: str) -> bool:
    try:
        match = resolve(path)
    except Resolver404:
        return False

    actions = getattr(match.func, "actions", None)
    if actions is not None:
        return method.lower() in actions

    view_class = getattr(match.func, "view_class", None)
    if view_class is None:
        return True
    return hasattr(view_class, method.lower())


@pytest.mark.parametrize("collection_path", API_COLLECTIONS, ids=lambda path: path.name)
def test_api_collection_files_exist_and_are_valid_json(collection_path):
    assert collection_path.exists()

    collection = _load_collection(collection_path)

    assert collection["base_url"] == "{{base_url}}"
    assert isinstance(collection["groups"], list)
    assert collection["groups"]


@pytest.mark.parametrize("collection_path", API_COLLECTIONS, ids=lambda path: path.name)
def test_api_collection_endpoint_contract_shape(collection_path):
    collection = _load_collection(collection_path)

    for group_name, endpoint in _iter_endpoints(collection):
        missing = REQUIRED_ENDPOINT_FIELDS - endpoint.keys()
        assert not missing, f"{collection_path.name}:{group_name}:{endpoint.get('name')} missing {missing}"

        assert isinstance(endpoint["name"], str) and endpoint["name"].strip()
        assert isinstance(endpoint["path"], str) and endpoint["path"].startswith("/")
        assert isinstance(endpoint["auth_required"], bool)

        methods = _split_methods(endpoint["method"])
        assert methods, f"{collection_path.name}:{endpoint['name']} has no methods"
        assert set(methods) <= VALID_HTTP_METHODS


@pytest.mark.parametrize("collection_path", API_COLLECTIONS, ids=lambda path: path.name)
def test_api_collection_paths_are_syntactically_valid(collection_path):
    collection = _load_collection(collection_path)

    for _, endpoint in _iter_endpoints(collection):
        parsed = urlsplit(endpoint["path"])
        representative_path = _representative_path(endpoint["path"])

        assert parsed.path.startswith("/")
        assert " " not in parsed.path
        assert "//" not in parsed.path
        assert representative_path.startswith("/")
        assert "{{" not in representative_path
        assert "}}" not in representative_path


@pytest.mark.parametrize("collection_path", API_COLLECTIONS, ids=lambda path: path.name)
def test_documented_api_routes_are_registered_for_declared_methods(collection_path):
    collection = _load_collection(collection_path)

    for _, endpoint in _iter_endpoints(collection):
        for method in _split_methods(endpoint["method"]):
            candidates = _candidate_paths(endpoint, method)
            assert any(_resolved_route_supports_method(path, method) for path in candidates), (
                f"{collection_path.name}:{endpoint['name']} declares {method} {endpoint['path']} "
                f"but no registered route accepted representative paths {candidates}"
            )


def test_unified_success_response_envelope():
    response = success_response(data={"id": 1}, message="Created", status_code=201)

    assert response.status_code == 201
    assert response.data == {
        "success": True,
        "message": "Created",
        "data": {"id": 1},
    }


def test_unified_error_response_envelope():
    response = error_response(message="Validation error", errors={"field": ["Required"]}, status_code=400)

    assert response.status_code == 400
    assert response.data == {
        "success": False,
        "message": "Validation error",
        "errors": {"field": ["Required"]},
    }
