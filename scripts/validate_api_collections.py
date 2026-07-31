"""Validate canonical Postman collections against the committed OpenAPI schema."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "docs" / "api" / "openapi.json"
COLLECTIONS = [
    ROOT / "integrations" / "api" / "panorama-dashboard-api.postman_collection.json",
    ROOT / "integrations" / "api" / "panorama-mobile-api.postman_collection.json",
]
METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
FORBIDDEN = ("secret_key", "database_url", "redis://:", "gmail-app-password", "b:/", "c:/")


def walk(items: list[dict]) -> list[dict]:
    requests: list[dict] = []
    for item in items:
        if "request" in item:
            requests.append(item)
        requests.extend(walk(item.get("item", [])))
    return requests


def main() -> int:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    known = {
        (method.upper(), path)
        for path, path_item in schema["paths"].items()
        for method in path_item
        if method.upper() in METHODS
    }
    actual: set[tuple[str, str]] = set()
    errors: list[str] = []
    for collection_path in COLLECTIONS:
        collection = json.loads(collection_path.read_text(encoding="utf-8"))
        if (
            collection.get("info", {}).get("schema")
            != "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
        ):
            errors.append(f"{collection_path}: incorrect Postman schema")
        text = collection_path.read_text(encoding="utf-8").lower()
        if any(value in text for value in FORBIDDEN):
            errors.append(f"{collection_path}: contains a forbidden secret/path marker")
        for item in walk(collection.get("item", [])):
            request = item["request"]
            method = request.get("method", "")
            raw = request.get("url", {}).get("raw", "")
            path = raw.replace("{{base_url}}", "").replace("{{", "{").replace("}}", "}")
            if path.endswith("/") and path != "/":
                path = path
            pair = (method, path)
            actual.add(pair)
            if pair not in known:
                errors.append(f"{collection_path}: unknown OpenAPI operation {method} {path}")
    missing = known - actual
    if missing:
        errors.append(
            "operations absent from both canonical collections: " + ", ".join(f"{m} {p}" for m, p in sorted(missing))
        )
    if errors:
        print("\n".join(errors))
        return 1
    print(f"collections valid; OpenAPI operations={len(known)}, covered={len(actual)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
