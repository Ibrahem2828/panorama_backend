"""Generate an auditable API contract matrix from the canonical OpenAPI schema."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "docs" / "api" / "openapi.json"
OUTPUT_PATH = ROOT / "docs" / "reports" / "API_CONTRACT_COVERAGE_MATRIX.md"
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}

PUBLIC_PREFIXES = ("/api/v1/health/",)
PUBLIC_PATHS = {
    "/api/v1/mobile/bootstrap/",
    "/api/v1/mobile/update-policy/",
    "/api/v1/policies/current/",
}


def consumer_for(path: str) -> str:
    if "/dashboard/" in path:
        return "Dashboard"
    if path.startswith(PUBLIC_PREFIXES) or path.startswith("/api/v1/auth/"):
        return "Dashboard + Mobile"
    return "Mobile"


def authentication_for(path: str, operation: dict[str, Any]) -> str:
    security = operation.get("security")
    if (
        path.startswith(PUBLIC_PREFIXES)
        or security in (None, [])
        or any(requirement == {} for requirement in security)
    ):
        return "Public"
    return "Bearer JWT"


def capability_for(path: str) -> str:
    rules = (
        ("/dashboard/audit", "audit.view"),
        ("/dashboard/users", "users.manage"),
        ("/dashboard/printing", "printing.manage"),
        ("/dashboard/support", "support.manage"),
        ("/dashboard/verification", "verification.review"),
        ("/dashboard/lectures", "lectures.manage"),
        ("/dashboard/feature", "product.manage"),
        ("/dashboard/maintenance", "product.manage"),
        ("/dashboard/mobile", "product.manage"),
        ("/dashboard/notifications", "product.manage"),
        ("/dashboard/feedback", "feedback.manage"),
        ("/dashboard/announcements", "announcements.manage"),
        ("/dashboard/groups", "groups.manage"),
        ("/dashboard/files", "files.manage"),
        ("/dashboard/universities", "academic.manage"),
        ("/dashboard/faculties", "academic.manage"),
        ("/dashboard/majors", "academic.manage"),
        ("/dashboard/subjects", "academic.manage"),
    )
    for prefix, capability in rules:
        if path.startswith(prefix):
            return capability
    return "Object/endpoint policy — REVIEW"


def request_schema(operation: dict[str, Any]) -> str:
    content = operation.get("requestBody", {}).get("content", {})
    refs = []
    for media_type, media in content.items():
        schema = media.get("schema", {})
        refs.append(f"{media_type}: {schema.get('$ref', schema.get('type', 'inline'))}")
    return "; ".join(refs) or "—"


def response_summary(operation: dict[str, Any]) -> tuple[str, str]:
    responses = operation.get("responses", {})
    success = ", ".join(code for code in responses if code.startswith("2")) or "—"
    errors = ", ".join(code for code in responses if not code.startswith("2")) or "Envelope/global handling — REVIEW"
    return success, errors


def idempotency_for(method: str, path: str, operation: dict[str, Any]) -> str:
    description = (operation.get("description", "") + operation.get("summary", "")).lower()
    if (
        "idempotency" in description
        or "idempotency" in path
        or path
        in {
            "/api/v1/printing/orders/",
            "/api/v1/auth/register/normal/",
            "/api/v1/auth/register/student/",
        }
    ):
        return "Required"
    if method in {"post", "put", "patch", "delete"}:
        return "Review write semantics"
    return "Not applicable"


def markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def main() -> None:
    schema: dict[str, Any] = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    rows: list[str] = []
    operation_count = 0
    for path, path_item in sorted(schema.get("paths", {}).items()):
        for method, operation in sorted(path_item.items()):
            if method not in HTTP_METHODS:
                continue
            operation_count += 1
            success, errors = response_summary(operation)
            values = [
                operation.get("operationId", "MISSING"),
                method.upper(),
                path,
                authentication_for(path, operation),
                capability_for(path),
                request_schema(operation),
                success,
                errors,
                idempotency_for(method, path, operation),
                consumer_for(path),
                "REVIEW: map behavioral test",
                "REVIEW",
            ]
            rows.append("| " + " | ".join(markdown_cell(str(value)) for value in values) + " |")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# API Contract Coverage Matrix\n\n"
        "Generated from docs/api/openapi.json; do not manually mark a row PASS. "
        "REVIEW means that OpenAPI cannot prove runtime authorization, object-level ownership, "
        "or behavioral test coverage.\n\n"
        f"**Operations:** {operation_count}\n\n"
        "| operationId | method | path | authentication | required capability | request schema | success response | error responses | idempotency requirement | consumer | test file | status |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|---|\n"
    )
    OUTPUT_PATH.write_text(header + "\n".join(rows) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT_PATH.relative_to(ROOT)} with {operation_count} operations")


if __name__ == "__main__":
    main()
