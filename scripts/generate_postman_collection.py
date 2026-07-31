"""Generate a minimal Postman collection directly from the committed OpenAPI schema."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "docs" / "api" / "openapi.json"
OUTPUT_PATH = ROOT / "docs" / "api" / "postman_collection.json"


def request_item(path: str, method: str, operation: dict) -> dict:
    headers = [{"key": "Accept", "value": "application/json"}]
    if operation.get("security"):
        headers.append({"key": "Authorization", "value": "Bearer {{accessToken}}", "type": "text"})
    request = {
        "method": method.upper(),
        "header": headers,
        "url": {"raw": "{{baseUrl}}" + path, "host": ["{{baseUrl}}"], "path": path.strip("/").split("/")},
        "description": operation.get("description", ""),
    }
    if method.lower() in {"post", "put", "patch"}:
        request["body"] = {"mode": "raw", "raw": "{}", "options": {"raw": {"language": "json"}}}
        headers.append({"key": "Content-Type", "value": "application/json"})
    return {"name": operation.get("operationId") or f"{method.upper()} {path}", "request": request}


def main() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    items = []
    for path, operations in sorted(schema.get("paths", {}).items()):
        for method, operation in sorted(operations.items()):
            if method.lower() in {"get", "post", "put", "patch", "delete"}:
                items.append(request_item(path, method, operation))
    collection = {
        "info": {
            "name": "Panorama API (generated from OpenAPI)",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "variable": [
            {"key": "baseUrl", "value": "http://localhost:8000"},
            {"key": "accessToken", "value": ""},
        ],
        "item": items,
    }
    OUTPUT_PATH.write_text(json.dumps(collection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
