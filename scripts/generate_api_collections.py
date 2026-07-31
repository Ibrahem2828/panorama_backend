"""Generate the two canonical Postman v2.1 collections from the committed OpenAPI schema.

The classifier is intentionally path-based and conservative: every documented HTTP
operation appears in the mobile collection, dashboard-only operations additionally
appear in the dashboard collection, and no route is invented by this generator.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "docs" / "api" / "openapi.json"
OUTPUT_DIR = ROOT / "integrations" / "api"
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}

COMMON_VARIABLES = [
    ("base_url", "http://localhost:8000"),
    ("access_token", ""),
    ("refresh_token", ""),
    ("request_id", ""),
    ("user_id", ""),
    ("faculty_id", ""),
    ("department_id", ""),
    ("course_id", ""),
    ("lecture_id", ""),
    ("page_number", "1"),
    ("note_id", ""),
    ("notification_id", ""),
    ("ticket_id", ""),
    ("installation_id", ""),
    ("idempotency_key", ""),
    ("app_version", "0.0.0"),
    ("app_build", "0"),
]

DASHBOARD_FOLDERS = [
    "00 - Health and Diagnostics",
    "01 - Authentication",
    "02 - Dashboard Bootstrap",
    "03 - Users",
    "04 - Roles and Permissions",
    "05 - Faculties and Departments",
    "06 - Courses and Curriculum",
    "07 - Lectures",
    "08 - Lecture Processing",
    "09 - Lecture Viewer Administration",
    "10 - Notifications",
    "11 - Support and Tickets",
    "12 - Feedback",
    "13 - Mobile Release Policies",
    "14 - Maintenance Mode",
    "15 - Feature Flags",
    "16 - Policies and Consents",
    "17 - Device Installations",
    "18 - Audit Logs",
    "19 - System Settings",
    "20 - Reports",
    "21 - Account and Security",
]

MOBILE_FOLDERS = [
    "00 - Mobile Bootstrap",
    "01 - App Version and Update Policy",
    "02 - Maintenance and Remote Configuration",
    "03 - Authentication and OTP",
    "04 - Session and Tokens",
    "05 - Device Installation",
    "06 - User Profile",
    "07 - Policies and Consent",
    "08 - Faculties and Departments",
    "09 - Courses",
    "10 - Lectures",
    "11 - Lecture Viewer Sessions",
    "12 - Viewer Pages and Text",
    "13 - Lecture Notes and Bookmarks",
    "14 - Notifications",
    "15 - Support and Tickets",
    "16 - Feedback and Ratings",
    "17 - Chat if implemented",
    "18 - Account Security",
    "19 - Account Deletion",
    "20 - Sync and Incremental Updates",
]


def folder_for_dashboard(path: str) -> str:
    if path.startswith("/api/v1/health/"):
        return "00 - Health and Diagnostics"
    if path.startswith("/api/v1/auth/"):
        return "01 - Authentication"
    if "/dashboard/stats" in path or "/dashboard/capabilities" in path:
        return "02 - Dashboard Bootstrap"
    if "/dashboard/users" in path:
        return "03 - Users"
    if "permission" in path:
        return "04 - Roles and Permissions"
    if any(part in path for part in ("universities", "faculties", "majors")):
        return "05 - Faculties and Departments"
    if any(part in path for part in ("subjects", "academic-years", "semesters")):
        return "06 - Courses and Curriculum"
    if "lectures" in path and "processing" in path:
        return "08 - Lecture Processing"
    if "lectures" in path and "viewer" in path:
        return "09 - Lecture Viewer Administration"
    if "lectures" in path:
        return "07 - Lectures"
    if "notifications" in path:
        return "10 - Notifications"
    if "support" in path:
        return "11 - Support and Tickets"
    if "feedback" in path:
        return "12 - Feedback"
    if "mobile-release" in path:
        return "13 - Mobile Release Policies"
    if "maintenance" in path:
        return "14 - Maintenance Mode"
    if "feature-flags" in path:
        return "15 - Feature Flags"
    if "terms-versions" in path or "privacy-policy" in path or "policies" in path:
        return "16 - Policies and Consents"
    if "devices" in path:
        return "17 - Device Installations"
    if "audit" in path:
        return "18 - Audit Logs"
    if "account" in path or "auth" in path:
        return "21 - Account and Security"
    return "19 - System Settings"


def folder_for_mobile(path: str) -> str:
    if path == "/api/v1/mobile/bootstrap/":
        return "00 - Mobile Bootstrap"
    if path == "/api/v1/mobile/update-policy/":
        return "01 - App Version and Update Policy"
    if "maintenance" in path or "feature-flags" in path:
        return "02 - Maintenance and Remote Configuration"
    if path.startswith("/api/v1/auth/") and any(key in path for key in ("login", "register", "otp", "password-reset")):
        return "03 - Authentication and OTP"
    if path.startswith("/api/v1/auth/"):
        return "04 - Session and Tokens"
    if "/mobile/devices/" in path:
        return "05 - Device Installation"
    if path.endswith("/auth/me/"):
        return "06 - User Profile"
    if "/policies/" in path:
        return "07 - Policies and Consent"
    if any(part in path for part in ("universities", "faculties", "majors", "academic-years", "semesters")):
        return "08 - Faculties and Departments"
    if "subjects" in path or "courses" in path:
        return "09 - Courses"
    if "lectures" in path and "/notes" in path:
        return "13 - Lecture Notes and Bookmarks"
    if "lectures" in path and "/viewer/session" in path:
        return "11 - Lecture Viewer Sessions"
    if "lectures" in path and "/viewer/" in path:
        return "12 - Viewer Pages and Text"
    if "lectures" in path:
        return "10 - Lectures"
    if "notifications" in path:
        return "14 - Notifications"
    if "support" in path:
        return "15 - Support and Tickets"
    if "feedback" in path:
        return "16 - Feedback and Ratings"
    if "groups" in path or "chat" in path:
        return "17 - Chat if implemented"
    if "account/deletion" in path:
        return "19 - Account Deletion"
    return "20 - Sync and Incremental Updates"


def operation_is_public(path: str) -> bool:
    return (
        path.startswith("/api/v1/health/")
        or path
        in {
            "/api/v1/mobile/bootstrap/",
            "/api/v1/mobile/update-policy/",
            "/api/v1/policies/current/",
        }
        or path.startswith("/api/v1/auth/")
    )


def build_item(path: str, method: str, operation: dict[str, Any], *, mobile: bool) -> dict[str, Any]:
    path_parts = [part if not part.startswith("{") else f"{{{{{part[1:-1]}}}}}" for part in path.strip("/").split("/")]
    replaced_path = "/" + "/".join(path_parts) + "/"
    headers = [
        {"key": "Accept", "value": "application/json"},
        {"key": "X-Request-ID", "value": "{{request_id}}"},
    ]
    if not operation_is_public(path):
        headers.append({"key": "Authorization", "value": "Bearer {{access_token}}"})
    if mobile:
        headers.extend(
            [
                {"key": "X-App-Platform", "value": "android"},
                {"key": "X-App-Version", "value": "{{app_version}}"},
                {"key": "X-App-Build", "value": "{{app_build}}"},
                {"key": "X-Installation-ID", "value": "{{installation_id}}"},
                {"key": "X-Device-Locale", "value": "ar"},
            ]
        )
    if method in {"post", "put", "patch"}:
        headers.append({"key": "Content-Type", "value": "application/json"})
        if "idempotency" in (operation.get("description", "") + path).lower() or method == "post":
            headers.append({"key": "Idempotency-Key", "value": "{{idempotency_key}}"})
    item = {
        "name": operation.get("operationId") or f"{method.upper()} {path}",
        "request": {
            "method": method.upper(),
            "header": headers,
            "url": {"raw": "{{base_url}}" + replaced_path, "host": ["{{base_url}}"], "path": path_parts},
            "description": (
                f"العقد الفعلي: {method.upper()} {path}. "
                f"Permission and schema are authoritative in OpenAPI. {operation.get('summary', '')}"
            ),
        },
        "event": [
            {
                "listen": "test",
                "script": {
                    "type": "text/javascript",
                    "exec": [
                        "pm.test('JSON response when present', function () { pm.expect(pm.response.headers.get('Content-Type') || '').to.include('application/json'); });",
                        "pm.test('request correlation header', function () { pm.expect(pm.response.headers.has('X-Request-ID')).to.eql(true); });",
                        "pm.test('no stack trace is exposed', function () { pm.expect(pm.response.text()).to.not.include('Traceback'); });",
                        "if (pm.response.code >= 400) { pm.test('stable error envelope', function () { var b=pm.response.json(); pm.expect(b.success).to.eql(false); pm.expect(b.code).to.be.a('string'); }); }",
                    ],
                },
            }
        ],
    }
    if method in {"post", "put", "patch"}:
        item["request"]["body"] = {"mode": "raw", "raw": "{}", "options": {"raw": {"language": "json"}}}
    return item


def build_collection(name: str, folders: list[str], groups: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    return {
        "info": {
            "name": name,
            "description": "Generated from docs/api/openapi.json. Do not add secrets; regenerate after an API contract change.",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "variable": [{"key": key, "value": value} for key, value in COMMON_VARIABLES],
        "event": [
            {
                "listen": "prerequest",
                "script": {
                    "type": "text/javascript",
                    "exec": [
                        "pm.collectionVariables.set('request_id', pm.variables.replaceIn('{{$guid}}'));",
                        "if (!pm.collectionVariables.get('idempotency_key')) { pm.collectionVariables.set('idempotency_key', pm.variables.replaceIn('{{$guid}}')); }",
                    ],
                },
            }
        ],
        "item": [{"name": folder, "item": groups.get(folder, [])} for folder in folders],
    }


def main() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    dashboard_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    mobile_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path, path_item in sorted(schema.get("paths", {}).items()):
        for method, operation in sorted(path_item.items()):
            if method not in HTTP_METHODS:
                continue
            if "/dashboard/" in path:
                dashboard_groups[folder_for_dashboard(path)].append(build_item(path, method, operation, mobile=False))
            elif path.startswith("/api/v1/health/") or path.startswith("/api/v1/auth/"):
                dashboard_groups[folder_for_dashboard(path)].append(build_item(path, method, operation, mobile=False))
            if "/dashboard/" not in path:
                mobile_groups[folder_for_mobile(path)].append(build_item(path, method, operation, mobile=True))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "panorama-dashboard-api.postman_collection.json").write_text(
        json.dumps(
            build_collection("Panorama Dashboard API", DASHBOARD_FOLDERS, dashboard_groups),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (OUTPUT_DIR / "panorama-mobile-api.postman_collection.json").write_text(
        json.dumps(build_collection("Panorama Mobile API", MOBILE_FOLDERS, mobile_groups), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
