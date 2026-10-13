from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest
from rest_framework.test import APIClient

from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.announcements.models import Announcement
from apps.audit.models import AuditAction, AuditLog
from apps.printing.models import PrintPickupLocation
from apps.product.models import MaintenanceMode
from apps.universities.models import AcademicYear, Faculty, Major, Semester, University

pytestmark = pytest.mark.django_db

A = AuditAction


@pytest.fixture
def admin():
    return User.objects.create_user(
        full_name="Audit Admin",
        email="audit-admin@example.test",
        phone_number="+963955700001",
        password="StrongPass123!",
        role=UserRole.ADMIN,
    )


@pytest.fixture
def client(admin):
    api_client = APIClient()
    api_client.force_authenticate(admin)
    api_client.raise_request_exception = False
    return api_client


@pytest.fixture
def ctx():
    university = University.objects.create(name="Ctx University", code="CTXU")
    faculty = Faculty.objects.create(university=university, name="Ctx Faculty", code="CF")
    major = Major.objects.create(faculty=faculty, name="Ctx Major", code="CM")
    year = AcademicYear.objects.create(name="Ctx Year", order=1)
    semester = Semester.objects.create(name="Ctx Semester", order=1)
    return SimpleNamespace(university=university, faculty=faculty, major=major, year=year, semester=semester)


def _case(url, target_type, create, update, create_action, update_action, delete_action, changed):
    return {
        "url": url,
        "target_type": target_type,
        "create": create,
        "update": update,
        "actions": (create_action, update_action, delete_action),
        "changed": changed,
    }


def _cases(ctx):
    academic = (A.ACADEMIC_ENTITY_CREATED, A.ACADEMIC_ENTITY_UPDATED, A.ACADEMIC_ENTITY_DELETED)
    return {
        "universities": _case(
            "/api/v1/dashboard/universities/",
            "University",
            {"name": "New U", "code": "NEWU"},
            {"is_active": False},
            *academic,
            ["is_active"],
        ),
        "faculties": _case(
            "/api/v1/dashboard/faculties/",
            "Faculty",
            {"university": ctx.university.id, "name": "New F", "code": "NF"},
            {"name": "Renamed F"},
            *academic,
            ["name"],
        ),
        "majors": _case(
            "/api/v1/dashboard/majors/",
            "Major",
            {"faculty": ctx.faculty.id, "name": "New M", "code": "NM"},
            {"is_active": False},
            *academic,
            ["is_active"],
        ),
        "subjects": _case(
            "/api/v1/dashboard/subjects/",
            "Subject",
            {
                "major": ctx.major.id,
                "academic_year": ctx.year.id,
                "semester": ctx.semester.id,
                "name": "New S",
                "code": "NS",
                "description": "A long private-ish description",
            },
            {"is_active": False},
            *academic,
            ["is_active"],
        ),
        "academic-years": _case(
            "/api/v1/dashboard/academic-years/",
            "AcademicYear",
            {"name": "Year 9", "order": 9},
            {"is_active": False},
            *academic,
            ["is_active"],
        ),
        "semesters": _case(
            "/api/v1/dashboard/semesters/",
            "Semester",
            {"name": "Semester 9", "order": 9},
            {"is_active": False},
            *academic,
            ["is_active"],
        ),
        "announcements": _case(
            "/api/v1/dashboard/announcements/",
            "Announcement",
            {"title": "Hello", "description": "Body text that must never be copied into the audit trail"},
            {"is_active": False},
            A.ANNOUNCEMENT_CREATED,
            A.ANNOUNCEMENT_UPDATED,
            A.ANNOUNCEMENT_DELETED,
            ["is_active"],
        ),
        "print-pricing-rules": _case(
            "/api/v1/dashboard/printing/pricing-rules/",
            "PrintPricingRule",
            {
                "name": "A4 BW",
                "color_mode": "black_white",
                "paper_size": "A4",
                "sides": "one_sided",
                "price_per_sheet": "10.00",
            },
            {"price_per_sheet": "12.00"},
            *(A.PRINT_PRICING_CHANGED,) * 3,
            ["price_per_sheet"],
        ),
        "print-binding-prices": _case(
            "/api/v1/dashboard/printing/binding-prices/",
            "PrintBindingPrice",
            {"binding": "spiral", "price_per_copy": "500.00"},
            {"price_per_copy": "600.00"},
            *(A.PRINT_PRICING_CHANGED,) * 3,
            ["price_per_copy"],
        ),
        "print-pickup-locations": _case(
            "/api/v1/dashboard/printing/pickup-locations/",
            "PrintPickupLocation",
            {"name": "Main desk"},
            {"is_active": False},
            *(A.PRINT_PRICING_CHANGED,) * 3,
            ["is_active"],
        ),
        "maintenance": _case(
            "/api/v1/dashboard/maintenance-modes/",
            "MaintenanceMode",
            {"enabled": True, "title_en": "Down", "message_en": "Back soon"},
            {"enabled": False},
            *(A.MAINTENANCE_MODE_UPDATED,) * 3,
            ["enabled"],
        ),
        "feature-flags": _case(
            "/api/v1/dashboard/feature-flags/",
            "FeatureFlag",
            {"key": "chat_enabled", "enabled": True, "platform": "android", "role": "student"},
            {"enabled": False},
            *(A.FEATURE_FLAG_UPDATED,) * 3,
            ["enabled"],
        ),
        "release-policies": _case(
            "/api/v1/dashboard/mobile-release-policies/",
            "MobileAppReleasePolicy",
            {
                "platform": "android",
                "latest_version": "1.0.0",
                "latest_build": 5,
                "minimum_supported_build": 1,
                "update_mode": "none",
            },
            {"update_mode": "required", "minimum_supported_build": 3},
            *(A.MOBILE_RELEASE_POLICY_UPDATED,) * 3,
            ["minimum_supported_build", "update_mode"],
        ),
        "terms-versions": _case(
            "/api/v1/dashboard/terms-versions/",
            "TermsVersion",
            {"version": "2026-01", "content_url": "https://example.test/terms"},
            {"is_active": False},
            A.POLICY_VERSION_PUBLISHED,
            A.POLICY_VERSION_UNPUBLISHED,
            A.POLICY_VERSION_UNPUBLISHED,
            ["is_active"],
        ),
        "privacy-versions": _case(
            "/api/v1/dashboard/privacy-policy-versions/",
            "PrivacyPolicyVersion",
            {"version": "2026-01", "requires_reacceptance": True},
            {"requires_reacceptance": False},
            A.POLICY_VERSION_PUBLISHED,
            A.POLICY_VERSION_UPDATED,
            A.POLICY_VERSION_UNPUBLISHED,
            ["requires_reacceptance"],
        ),
    }


CASE_KEYS = [
    "universities",
    "faculties",
    "majors",
    "subjects",
    "academic-years",
    "semesters",
    "announcements",
    "print-pricing-rules",
    "print-binding-prices",
    "print-pickup-locations",
    "maintenance",
    "feature-flags",
    "release-policies",
    "terms-versions",
    "privacy-versions",
]


@pytest.mark.parametrize("key", CASE_KEYS)
def test_each_dashboard_write_writes_exactly_one_audit_row(client, admin, ctx, key):
    case = _cases(ctx)[key]
    create_action, update_action, delete_action = case["actions"]

    created = client.post(case["url"], case["create"], format="json")
    assert created.status_code == 201, created.content
    object_id = str(created.data["data"]["id"])
    rows = list(AuditLog.objects.filter(target_type=case["target_type"], target_id=object_id))
    assert [row.action for row in rows] == [create_action]
    assert rows[0].actor_id == admin.id
    assert rows[0].ip_address is not None or rows[0].user_agent == ""

    before = AuditLog.objects.count()
    updated = client.patch(f"{case['url']}{object_id}/", case["update"], format="json")
    assert updated.status_code == 200, updated.content
    assert AuditLog.objects.count() == before + 1
    update_row = AuditLog.objects.filter(target_type=case["target_type"], target_id=object_id).order_by("-pk").first()
    assert update_row.action == update_action
    assert update_row.actor_id == admin.id
    assert update_row.new_value["changed_fields"] == case["changed"]

    before = AuditLog.objects.count()
    deleted = client.delete(f"{case['url']}{object_id}/")
    assert deleted.status_code in {200, 204}, deleted.content
    assert AuditLog.objects.count() == before + 1
    delete_row = AuditLog.objects.filter(target_type=case["target_type"], target_id=object_id).order_by("-pk").first()
    assert delete_row.action == delete_action
    assert delete_row.target_id == object_id
    assert delete_row.actor_id == admin.id
    assert delete_row.new_value == {"deleted": True}


def test_audit_payload_omits_long_text_and_sensitive_values(client):
    created = client.post(
        "/api/v1/dashboard/announcements/",
        {"title": "T", "description": "secret announcement body", "link": "https://example.test/x"},
        format="json",
    )
    assert created.status_code == 201
    row = AuditLog.objects.get(action=A.ANNOUNCEMENT_CREATED)
    assert "secret announcement body" not in str(row.new_value)
    assert row.new_value["title"] == "T"


def test_policy_version_unpublish_then_republish_actions(client):
    created = client.post("/api/v1/dashboard/terms-versions/", {"version": "v1"}, format="json")
    pk = created.data["data"]["id"]
    client.patch(f"/api/v1/dashboard/terms-versions/{pk}/", {"is_active": False}, format="json")
    client.patch(f"/api/v1/dashboard/terms-versions/{pk}/", {"is_active": True}, format="json")
    client.patch(f"/api/v1/dashboard/terms-versions/{pk}/", {"requires_reacceptance": True}, format="json")
    actions = list(
        AuditLog.objects.filter(target_type="TermsVersion", target_id=str(pk))
        .order_by("pk")
        .values_list("action", flat=True)
    )
    assert actions == [
        A.POLICY_VERSION_PUBLISHED,
        A.POLICY_VERSION_UNPUBLISHED,
        A.POLICY_VERSION_PUBLISHED,
        A.POLICY_VERSION_UPDATED,
    ]


def test_notification_campaign_is_audited_once(client, admin):
    response = client.post(
        "/api/v1/dashboard/notifications/campaign/",
        {"user_ids": [admin.id], "title": "Campaign", "body": "Hello", "deep_link": "/x"},
        format="json",
    )
    assert response.status_code == 201
    rows = AuditLog.objects.filter(action=A.NOTIFICATION_CAMPAIGN_CREATED)
    assert rows.count() == 1
    assert rows[0].new_value["recipient_count"] == 1 and "Hello" not in str(rows[0].new_value)


def test_failed_audit_rolls_back_the_business_change(client):
    with mock.patch("apps.audit.services.AuditLog.objects.create", side_effect=RuntimeError("audit down")):
        created = client.post("/api/v1/dashboard/announcements/", {"title": "T", "description": "D"}, format="json")
        assert created.status_code == 500
        maintenance = client.post("/api/v1/dashboard/maintenance-modes/", {"enabled": True}, format="json")
        assert maintenance.status_code == 500
    assert not Announcement.objects.exists()
    assert not MaintenanceMode.objects.exists()

    location = PrintPickupLocation.objects.create(name="Keep")
    with mock.patch("apps.audit.services.AuditLog.objects.create", side_effect=RuntimeError("audit down")):
        failed = client.patch(
            f"/api/v1/dashboard/printing/pickup-locations/{location.id}/", {"name": "Changed"}, format="json"
        )
    assert failed.status_code == 500
    location.refresh_from_db()
    assert location.name == "Keep"


def test_role_change_and_its_audit_commit_or_roll_back_together(client):
    student = User.objects.create_user(
        full_name="Target",
        email="target@example.test",
        phone_number="+963955700002",
        password="StrongPass123!",
        role=UserRole.STUDENT,
    )
    url = f"/api/v1/dashboard/users/{student.id}/"
    with mock.patch("apps.audit.services.AuditLog.objects.create", side_effect=RuntimeError("audit down")):
        failed = client.patch(url, {"role": UserRole.PRINT_STAFF}, format="json")
    assert failed.status_code == 500
    student.refresh_from_db()
    assert student.role == UserRole.STUDENT
    assert not AuditLog.objects.filter(action=A.USER_ROLE_CHANGED).exists()

    ok = client.patch(url, {"role": UserRole.PRINT_STAFF}, format="json")
    assert ok.status_code == 200
    student.refresh_from_db()
    assert student.role == UserRole.PRINT_STAFF
    row = AuditLog.objects.get(action=A.USER_ROLE_CHANGED)
    assert row.old_value["role"] == UserRole.STUDENT and row.new_value["role"] == UserRole.PRINT_STAFF
