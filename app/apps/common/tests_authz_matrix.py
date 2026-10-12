"""Authorization matrix for every ``/api/v1/`` route.

Three layers, so a route cannot ship without an explicit authorization decision:

1. ``ROUTE_KINDS`` classifies EVERY route template as ``public``, ``auth``, ``student`` or ``dashboard``.
   ``test_every_route_is_classified`` fails (listing the routes) when a new route is added without a classification
   or a classified route disappears, and ``test_classification_matches_view_permission_classes`` fails when the
   declared kind disagrees with the view's ``permission_classes``.
2. A generic sweep per kind: anonymous -> 401 everywhere that is not public; ``dashboard`` routes -> 403 for normal
   users, students and every staff role that lacks the route's capability.
3. ``IDOR_CASES`` is an explicit, readable table of object-level cases: a second student must not read or change
   the first student's objects (support, print orders, notifications, feedback, lecture notes, group chat, files,
   device registrations, protected-asset tickets) while the owner still can (positive control).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import URLPattern, URLResolver, get_resolver
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework.views import APIView

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.accounts.permissions import (
    ROLE_CAPABILITIES,
    CapabilityPermission,
    IsStudent,
    IsVerifiedStudent,
)
from apps.announcements.models import Announcement, AnnouncementTargetUserType
from apps.chat.models import Message
from apps.feedback.models import AppFeedback, FeedbackContext, FeedbackKind, FeedbackStatus
from apps.files.models import FileAccessTicket, FileResource, FileVisibility
from apps.groups.models import Group, GroupMembership, GroupMembershipStatus
from apps.lectures.models import Lecture, LectureNote, LectureProcessingStatus
from apps.notifications.models import Notification
from apps.printing.models import PrintOrder, PrintOrderItem
from apps.product.models import DeviceInstallation
from apps.support.models import SupportAttachmentAccessTicket, SupportTicket, SupportTicketMessage
from apps.universities.models import AcademicYear, Faculty, Major, Semester, Subject, University
from apps.verification.models import VerificationRequest

PUBLIC, AUTH, STUDENT, DASHBOARD = "public", "auth", "student", "dashboard"

#: Every route template under /api/v1/ and the decision made about it. Add new routes here, consciously.
ROUTE_KINDS: dict[str, str] = {
    # --- accounts / auth ---------------------------------------------------------------------------------------
    "auth/register/normal/": PUBLIC,
    "auth/register/student/": PUBLIC,
    "auth/login/": PUBLIC,
    "auth/token/refresh/": PUBLIC,
    "auth/otp/send/": PUBLIC,
    "auth/otp/verify/": PUBLIC,
    "auth/request-password-reset/": PUBLIC,
    "auth/confirm-password-reset/": PUBLIC,
    "auth/logout/": AUTH,
    "auth/me/": AUTH,
    "auth/change-password/": AUTH,
    "students/me/profile/": AUTH,
    "students/student-number/parse/": AUTH,
    # --- ops / public reference data -----------------------------------------------------------------------------
    "health/": PUBLIC,
    "health/live/": PUBLIC,
    "health/db/": PUBLIC,
    "health/ready/": PUBLIC,
    "health/startup/": PUBLIC,
    "universities/": PUBLIC,
    "universities/{pk}/": PUBLIC,
    "academic-years/": PUBLIC,
    "academic-years/{pk}/": PUBLIC,
    "semesters/": PUBLIC,
    "semesters/{pk}/": PUBLIC,
    "universities/{university_pk}/faculties/": PUBLIC,
    "faculties/{faculty_pk}/majors/": PUBLIC,
    "majors/{major_pk}/subjects/": PUBLIC,
    "mobile/bootstrap/": PUBLIC,
    "mobile/update-policy/": PUBLIC,
    "policies/current/": PUBLIC,
    # Redirect endpoint secured by a one-time, user-bound ticket (checked inside the view).
    "external-channels/open/{token}/": PUBLIC,
    # --- routers' api-root pages ----------------------------------------------------------------------------------
    "": AUTH,
    "dashboard/": DASHBOARD,
    # --- verification ---------------------------------------------------------------------------------------------
    "verification/submit/": STUDENT,
    "verification/resubmit/": STUDENT,
    "verification/me/": STUDENT,
    "verification-card-access/{token}/": DASHBOARD,
    # --- groups / chat ----------------------------------------------------------------------------------------------
    "groups/available/": STUDENT,
    "groups/available/{pk}/": STUDENT,
    "groups/my/": STUDENT,
    "groups/my/{pk}/": STUDENT,
    "groups/": STUDENT,
    "groups/{pk}/": STUDENT,
    "groups/{pk}/join/": STUDENT,
    "groups/{pk}/leave/": STUDENT,
    "groups/{pk}/whatsapp-ticket/": STUDENT,
    "groups/{group_id}/chat-ticket/": AUTH,
    "groups/{group_id}/messages/": AUTH,
    "groups/{group_id}/messages/{pk}/": AUTH,
    "groups/{group_id}/messages/{message_id}/delete/": AUTH,
    "groups/{group_id}/messages/{message_id}/report/": AUTH,
    "groups/{group_id}/messages/{message_id}/attachment-ticket/": AUTH,
    "protected-chat-attachments/{token}/": AUTH,
    # --- files --------------------------------------------------------------------------------------------------------
    "files/": AUTH,
    "files/{pk}/": AUTH,
    "groups/{group_pk}/files/": AUTH,
    "files/{pk}/access-ticket/": AUTH,
    "protected-files/{token}/": AUTH,
    # --- lectures -----------------------------------------------------------------------------------------------------
    "lectures/": AUTH,
    "lectures/{pk}/": AUTH,
    "lectures/{pk}/viewer/manifest/": AUTH,
    "lectures/{pk}/viewer/session/": AUTH,
    "lectures/{pk}/viewer/pages/{page_number}/": AUTH,
    "lectures/{pk}/viewer/pages/{page_number}/thumbnail/": AUTH,
    "lectures/{pk}/viewer/pages/{page_number}/text/": AUTH,
    "lectures/{pk}/processing-status/": AUTH,
    "lectures/{pk}/notes/": AUTH,
    "lectures/{lecture_id}/notes/{note_id}/": AUTH,
    # --- printing -----------------------------------------------------------------------------------------------------
    "printing/quote/": AUTH,
    "printing/orders/": AUTH,
    "printing/orders/my/": AUTH,
    "printing/orders/my/{pk}/": AUTH,
    "printing/pickup-locations/": AUTH,
    "printing/pickup-locations/{pk}/": AUTH,
    "printing/orders/{pk}/cancel/": AUTH,
    "printing/items/{pk}/access-ticket/": AUTH,
    "protected-print-items/{token}/": AUTH,
    # --- announcements / notifications -------------------------------------------------------------------------------
    "announcements/": AUTH,
    "announcements/{pk}/": AUTH,
    "notifications/": AUTH,
    "notifications/{pk}/": AUTH,
    "notifications/unread-count/": AUTH,
    "notifications/{pk}/read/": AUTH,
    "notifications/read-all/": AUTH,
    "notifications/preferences/": AUTH,
    "notifications/device-tokens/": AUTH,
    "notifications/device-tokens/{pk}/": AUTH,
    # --- support ------------------------------------------------------------------------------------------------------
    "support/tickets/": AUTH,
    "support/tickets/my/": AUTH,
    "support/tickets/{pk}/": AUTH,
    "support/tickets/{pk}/messages/": AUTH,
    "support/messages/{pk}/attachment-ticket/": AUTH,
    "support/attachments/{token}/": AUTH,
    # --- feedback -----------------------------------------------------------------------------------------------------
    "feedback/": AUTH,
    "feedback/prompt/": AUTH,
    "feedback/prompt-event/": AUTH,
    "feedback/{pk}/privacy-request/": AUTH,
    "feedback/{pk}/vote/": AUTH,
    "feedback/mine/": AUTH,
    "feedback/mine/{pk}/": AUTH,
    "feedback/suggestions/": AUTH,
    "feedback/suggestions/{pk}/": AUTH,
    # --- product / mobile / account lifecycle -----------------------------------------------------------------------
    "mobile/devices/register/": AUTH,
    "mobile/devices/{installation_id}/": AUTH,
    "mobile/devices/{installation_id}/revoke/": AUTH,
    "policies/accept/": AUTH,
    "account/deletion/request/": AUTH,
    "account/deletion/cancel/": AUTH,
    "account/deletion/status/": AUTH,
    # --- dashboard (capability-gated) ---------------------------------------------------------------------------------
    "dashboard/stats/": DASHBOARD,
    "dashboard/capabilities/": DASHBOARD,
    "dashboard/users/": DASHBOARD,
    "dashboard/users/{pk}/": DASHBOARD,
    "dashboard/users/{pk}/activate/": DASHBOARD,
    "dashboard/users/{pk}/deactivate/": DASHBOARD,
    "dashboard/users/{user_id}/permission-overrides/": DASHBOARD,
    "dashboard/universities/": DASHBOARD,
    "dashboard/universities/{pk}/": DASHBOARD,
    "dashboard/faculties/": DASHBOARD,
    "dashboard/faculties/{pk}/": DASHBOARD,
    "dashboard/majors/": DASHBOARD,
    "dashboard/majors/{pk}/": DASHBOARD,
    "dashboard/academic-years/": DASHBOARD,
    "dashboard/academic-years/{pk}/": DASHBOARD,
    "dashboard/semesters/": DASHBOARD,
    "dashboard/semesters/{pk}/": DASHBOARD,
    "dashboard/subjects/": DASHBOARD,
    "dashboard/subjects/{pk}/": DASHBOARD,
    "dashboard/verifications/": DASHBOARD,
    "dashboard/verifications/{pk}/": DASHBOARD,
    "dashboard/verifications/{pk}/approve/": DASHBOARD,
    "dashboard/verifications/{pk}/reject/": DASHBOARD,
    "dashboard/verifications/{pk}/needs-update/": DASHBOARD,
    "dashboard/verifications/{pk}/card-ticket/": DASHBOARD,
    "dashboard/groups/": DASHBOARD,
    "dashboard/groups/{pk}/": DASHBOARD,
    "dashboard/groups/{group_pk}/memberships/": DASHBOARD,
    "dashboard/groups/{group_pk}/join-requests/": DASHBOARD,
    "dashboard/groups/{pk}/whatsapp-channel/": DASHBOARD,
    "dashboard/group-memberships/{pk}/approve/": DASHBOARD,
    "dashboard/group-memberships/{pk}/reject/": DASHBOARD,
    "dashboard/group-memberships/{pk}/block/": DASHBOARD,
    "dashboard/group-memberships/{pk}/role/": DASHBOARD,
    "dashboard/files/": DASHBOARD,
    "dashboard/files/{pk}/": DASHBOARD,
    "dashboard/lectures/": DASHBOARD,
    "dashboard/lectures/{pk}/": DASHBOARD,
    "dashboard/lectures/{pk}/original/": DASHBOARD,
    "dashboard/printing/orders/": DASHBOARD,
    "dashboard/printing/orders/{pk}/": DASHBOARD,
    "dashboard/printing/orders/{pk}/assign/": DASHBOARD,
    "dashboard/printing/orders/{pk}/status/": DASHBOARD,
    "dashboard/printing/orders/{pk}/note/": DASHBOARD,
    "dashboard/printing/pricing-rules/": DASHBOARD,
    "dashboard/printing/pricing-rules/{pk}/": DASHBOARD,
    "dashboard/printing/binding-prices/": DASHBOARD,
    "dashboard/printing/binding-prices/{pk}/": DASHBOARD,
    "dashboard/printing/pickup-locations/": DASHBOARD,
    "dashboard/printing/pickup-locations/{pk}/": DASHBOARD,
    "dashboard/announcements/": DASHBOARD,
    "dashboard/announcements/{pk}/": DASHBOARD,
    "dashboard/notifications/campaign/": DASHBOARD,
    "dashboard/support/tickets/": DASHBOARD,
    "dashboard/support/tickets/{pk}/": DASHBOARD,
    "dashboard/support/tickets/{pk}/status/": DASHBOARD,
    "dashboard/support/tickets/{pk}/priority/": DASHBOARD,
    "dashboard/support/tickets/{pk}/assign/": DASHBOARD,
    "dashboard/support/tickets/{pk}/messages/": DASHBOARD,
    "dashboard/audit-logs/": DASHBOARD,
    "dashboard/audit-logs/{pk}/": DASHBOARD,
    "dashboard/feedback/": DASHBOARD,
    "dashboard/feedback/{pk}/": DASHBOARD,
    "dashboard/feedback/{pk}/workflow/": DASHBOARD,
    "dashboard/feedback-analytics/": DASHBOARD,
    "dashboard/feedback-prompt-policies/": DASHBOARD,
    "dashboard/feedback-prompt-policies/{pk}/": DASHBOARD,
    "dashboard/mobile-release-policies/": DASHBOARD,
    "dashboard/mobile-release-policies/{pk}/": DASHBOARD,
    "dashboard/maintenance-modes/": DASHBOARD,
    "dashboard/maintenance-modes/{pk}/": DASHBOARD,
    "dashboard/feature-flags/": DASHBOARD,
    "dashboard/feature-flags/{pk}/": DASHBOARD,
    "dashboard/terms-versions/": DASHBOARD,
    "dashboard/terms-versions/{pk}/": DASHBOARD,
    "dashboard/privacy-policy-versions/": DASHBOARD,
    "dashboard/privacy-policy-versions/{pk}/": DASHBOARD,
}

# ---------------------------------------------------------------------------------------------------------------------
# Route discovery
# ---------------------------------------------------------------------------------------------------------------------

_CAPTURE_RE = re.compile(r"\(\?P<(\w+)>[^)]*\)")
_CONVERTER_RE = re.compile(r"<(?:\w+:)?(\w+)>")
_UUID_PARAMS = {"token", "installation_id"}


@dataclass
class Route:
    template: str
    methods: set[str]
    view_classes: list[type[APIView]]

    def url(self) -> str:
        def fill(match):
            return str(uuid.uuid4()) if match.group(1) in _UUID_PARAMS else "999999"

        return "/api/v1/" + re.sub(r"\{(\w+)\}", fill, self.template)


def _normalise(raw: str) -> str:
    raw = _CAPTURE_RE.sub(lambda m: "{" + m.group(1) + "}", raw)
    raw = _CONVERTER_RE.sub(lambda m: "{" + m.group(1) + "}", raw)
    return raw.replace("^", "").replace("$", "")


def _methods(callback) -> set[str]:
    actions = getattr(callback, "actions", None)
    if actions:
        return {m.upper() for m in actions}
    cls = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
    if cls is None:
        return set()
    return {m.upper() for m in cls.http_method_names if m not in {"options", "head"} and hasattr(cls, m)}


def _walk(patterns, prefix=""):
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from _walk(pattern.url_patterns, prefix + str(pattern.pattern))
        elif isinstance(pattern, URLPattern):
            yield prefix + str(pattern.pattern), pattern.callback


def discover_routes() -> dict[str, Route]:
    routes: dict[str, Route] = {}
    for raw, callback in _walk(get_resolver().url_patterns):
        if not raw.startswith("api/v1/") or "format" in raw or r"\." in raw:
            continue
        template = _normalise(raw[len("api/v1/") :])
        route = routes.setdefault(template, Route(template, set(), []))
        route.methods |= _methods(callback)
        cls = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
        if cls is not None and cls not in route.view_classes:
            route.view_classes.append(cls)
    return routes


def _permission_types(view: type[APIView]) -> list[type]:
    """The view's permission classes as plain types (composed ``A | B`` operands are instances)."""
    return [p if isinstance(p, type) else type(p) for p in view.permission_classes]


def _derived_kind(route: Route) -> str:
    kinds = set()
    for cls in route.view_classes:
        perms = _permission_types(cls)
        if any(issubclass(p, CapabilityPermission) for p in perms):
            kinds.add(DASHBOARD)
        elif any(issubclass(p, (IsStudent, IsVerifiedStudent)) for p in perms):
            kinds.add(STUDENT)
        elif any(p.__name__ == "AllowAny" for p in perms):
            kinds.add(PUBLIC)
        else:
            kinds.add(AUTH)
    return kinds.pop() if len(kinds) == 1 else "/".join(sorted(kinds))


def _required_capabilities(route: Route) -> set[str]:
    caps: set[str] = set()
    for cls in route.view_classes:
        for perm in _permission_types(cls):
            if issubclass(perm, CapabilityPermission):
                caps.add(perm.capability)
    return caps


ROUTES = discover_routes()


def _sorted_ids(items):
    return sorted(items)


# ---------------------------------------------------------------------------------------------------------------------
# Layer 1: classification completeness
# ---------------------------------------------------------------------------------------------------------------------


def test_every_route_is_classified():
    unclassified = _sorted_ids(set(ROUTES) - set(ROUTE_KINDS))
    stale = _sorted_ids(set(ROUTE_KINDS) - set(ROUTES))
    problems = []
    if unclassified:
        problems.append(
            "UNCLASSIFIED API ROUTES (add each to ROUTE_KINDS in apps/common/tests_authz_matrix.py after deciding "
            "who may call it, and add object-level cases to IDOR_CASES when it takes an object id):\n  "
            + "\n  ".join(f"{t}  methods={sorted(ROUTES[t].methods)}" for t in unclassified)
        )
    if stale:
        problems.append("ROUTE_KINDS lists routes that no longer exist (remove them):\n  " + "\n  ".join(stale))
    assert not problems, "\n\n".join(problems)


def test_the_gate_reports_a_new_unclassified_route(monkeypatch):
    """Self-test: dropping a classification (== a new, unreviewed route) must fail with a helpful message."""
    assert len(ROUTES) > 100  # discovery really walked the URL conf
    trimmed = {k: v for k, v in ROUTE_KINDS.items() if k != "feedback/{pk}/vote/"}
    monkeypatch.setitem(globals(), "ROUTE_KINDS", trimmed)
    with pytest.raises(AssertionError) as excinfo:
        test_every_route_is_classified()
    assert "UNCLASSIFIED API ROUTES" in str(excinfo.value)
    assert "feedback/{pk}/vote/" in str(excinfo.value)


def test_classification_matches_view_permission_classes():
    mismatches = []
    for template, route in sorted(ROUTES.items()):
        declared = ROUTE_KINDS.get(template)
        if declared is None:
            continue  # reported by test_every_route_is_classified
        derived = _derived_kind(route)
        if declared != derived:
            mismatches.append(f"{template}: declared {declared!r} but the view's permission_classes imply {derived!r}")
        if template.startswith("dashboard/") and declared != DASHBOARD:
            mismatches.append(f"{template}: a dashboard/ path must be capability-gated, declared {declared!r}")
    assert not mismatches, "\n".join(mismatches)


# ---------------------------------------------------------------------------------------------------------------------
# Layer 2: generic sweep
# ---------------------------------------------------------------------------------------------------------------------


def _call(client: APIClient, method: str, url: str):
    return client.generic(method, url, data="{}", content_type="application/json")


def _user(role: str, n: int, *, verified: bool = False) -> User:
    user = User.objects.create_user(
        full_name=f"Matrix {role} {n}",
        email=f"matrix-{role}-{n}@example.test",
        phone_number=f"+96396600{n:04d}",
        password="StrongPass123!",
        role=role,
    )
    if role == UserRole.STUDENT:
        StudentProfile.objects.create(
            user=user,
            verification_status=StudentVerificationStatus.APPROVED if verified else StudentVerificationStatus.PENDING,
        )
    return user


def _client_for(user: User | None) -> APIClient:
    client = APIClient()
    if user is not None:
        client.force_authenticate(user)
    return client


@pytest.mark.django_db
def test_anonymous_gets_401_on_every_non_public_route():
    client = _client_for(None)
    failures = []
    for template, route in sorted(ROUTES.items()):
        if ROUTE_KINDS.get(template) in {None, PUBLIC}:
            continue
        for method in sorted(route.methods):
            response = _call(client, method, route.url())
            if response.status_code != 401:
                failures.append(f"{method} {template} -> {response.status_code} (expected 401)")
    assert not failures, "Anonymous access is not rejected:\n" + "\n".join(failures)


@pytest.mark.django_db
def test_public_routes_never_demand_authentication_but_never_crash():
    client = _client_for(None)
    failures = []
    for template, route in sorted(ROUTES.items()):
        if ROUTE_KINDS.get(template) != PUBLIC:
            continue
        for method in sorted(route.methods):
            response = _call(client, method, route.url())
            if response.status_code in {401, 403} or response.status_code >= 500 and response.status_code != 503:
                failures.append(f"{method} {template} -> {response.status_code}")
    assert not failures, "Public routes misbehave:\n" + "\n".join(failures)


@pytest.mark.django_db
def test_student_only_routes_reject_normal_users_and_unverified_students():
    normal = _client_for(_user(UserRole.NORMAL_USER, 1))
    unverified = _client_for(_user(UserRole.STUDENT, 2, verified=False))
    failures = []
    for template, route in sorted(ROUTES.items()):
        if ROUTE_KINDS.get(template) != STUDENT:
            continue
        needs_verified = any(any(p is IsVerifiedStudent for p in _permission_types(cls)) for cls in route.view_classes)
        for method in sorted(route.methods):
            if _call(normal, method, route.url()).status_code != 403:
                failures.append(f"normal user {method} {template} is not 403")
            if needs_verified and _call(unverified, method, route.url()).status_code != 403:
                failures.append(f"unverified student {method} {template} is not 403")
    assert not failures, "\n".join(failures)


DASHBOARD_ROLES = [
    UserRole.NORMAL_USER,
    UserRole.STUDENT,
    UserRole.PRINT_STAFF,
    UserRole.SUPPORT_STAFF,
    UserRole.CONTENT_MANAGER,
    UserRole.ADMIN,
    UserRole.IT_SUPPORT,
]


def _role_has(role: UserRole, capabilities: set[str]) -> bool:
    granted = ROLE_CAPABILITIES.get(role, set())
    return "*" in granted or capabilities <= granted


@pytest.mark.django_db
def test_dashboard_routes_enforce_capabilities_for_every_role():
    clients = {role: _client_for(_user(role, i, verified=True)) for i, role in enumerate(DASHBOARD_ROLES, start=10)}
    failures = []
    for template, route in sorted(ROUTES.items()):
        if ROUTE_KINDS.get(template) != DASHBOARD:
            continue
        required = _required_capabilities(route)
        assert required, f"{template} is classified dashboard but requires no capability"
        for method in sorted(route.methods):
            for role, client in clients.items():
                status = _call(client, method, route.url()).status_code
                if _role_has(role, required):
                    if status in {401, 403}:
                        failures.append(f"{role} should pass {method} {template} {sorted(required)} but got {status}")
                elif status != 403:
                    failures.append(f"{role} lacks {sorted(required)} on {method} {template}: got {status}, want 403")
    assert not failures, "Capability matrix violations:\n" + "\n".join(failures)


@pytest.mark.django_db
def test_dashboard_root_does_not_list_dashboard_endpoints_to_non_staff():
    response = _client_for(_user(UserRole.NORMAL_USER, 30)).get("/api/v1/dashboard/")
    assert response.status_code == 403 or "dashboard" not in str(response.data).lower()


# ---------------------------------------------------------------------------------------------------------------------
# Layer 3: object-level (IDOR) cases
# ---------------------------------------------------------------------------------------------------------------------

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x9a\xb1\xea\x00\x00\x00\x00IEND\xaeB`\x82"
)


class World:
    """Student A owns every object; student B (same curriculum, verified) must not reach them."""

    university: University
    major: Major
    other_major: Major
    admin: User
    normal: User
    a: User
    b: User
    ticket: SupportTicket
    ticket_message: SupportTicketMessage
    support_token: SupportAttachmentAccessTicket
    public_file: FileResource
    admin_file: FileResource
    group: Group
    group_file: FileResource
    group_file_token: FileAccessTicket
    order: PrintOrder
    item: PrintOrderItem
    message: Message
    notification: Notification
    rating: AppFeedback
    suggestion: AppFeedback
    lecture: Lecture
    note: LectureNote
    verification: VerificationRequest
    installation: DeviceInstallation
    admins_only_announcement: Announcement
    other_major_announcement: Announcement
    inactive_announcement: Announcement
    verified_announcement: Announcement


@pytest.fixture
def world(db) -> World:
    w = World()
    university = University.objects.create(name="Matrix University", code="MXU")
    faculty = Faculty.objects.create(university=university, name="Engineering", code="ENG")
    major = Major.objects.create(faculty=faculty, name="Software", code="SWE")
    other_major = Major.objects.create(faculty=faculty, name="Networks", code="NET")
    year = AcademicYear.objects.create(name="Year 1", order=1)
    semester = Semester.objects.create(name="Semester 1", order=1)
    subject = Subject.objects.create(major=major, academic_year=year, semester=semester, name="Algo", code="ALG")
    w.university, w.major, w.other_major = university, major, other_major

    w.admin = _user(UserRole.ADMIN, 50)
    w.normal = _user(UserRole.NORMAL_USER, 51)

    def student(n: int) -> User:
        user = User.objects.create_user(
            full_name=f"Student {n}",
            email=f"matrix-student-{n}@example.test",
            phone_number=f"+96396610{n:04d}",
            password="StrongPass123!",
            role=UserRole.STUDENT,
        )
        StudentProfile.objects.create(
            user=user,
            university=university,
            faculty=faculty,
            major=major,
            academic_year=year,
            semester=semester,
            verification_status=StudentVerificationStatus.APPROVED,
        )
        return user

    w.a, w.b = student(1), student(2)

    # support
    w.ticket = SupportTicket.objects.create(user=w.a, category="technical", subject="A private ticket")
    w.ticket_message = SupportTicketMessage.objects.create(
        ticket=w.ticket, sender=w.a, message="secret", attachment=ContentFile(b"%PDF-1.4 a", name="a.pdf")
    )
    w.support_token = SupportAttachmentAccessTicket.objects.create(
        message=w.ticket_message, requested_by=w.a, expires_at=timezone.now() + timedelta(minutes=5)
    )

    # files + printing
    w.public_file = FileResource.objects.create(
        title="Public", file=ContentFile(b"%PDF-1.4 p", name="p.pdf"), uploaded_by=w.admin, pages_count=1
    )
    w.admin_file = FileResource.objects.create(
        title="Admin only",
        file=ContentFile(b"%PDF-1.4 x", name="x.pdf"),
        uploaded_by=w.admin,
        visibility=FileVisibility.ADMIN_ONLY,
    )
    w.group = Group.objects.create(name="A's group", university=university, created_by=w.admin)
    GroupMembership.objects.create(group=w.group, user=w.a, status=GroupMembershipStatus.APPROVED)
    w.group_file = FileResource.objects.create(
        title="Group only",
        file=ContentFile(b"%PDF-1.4 g", name="g.pdf"),
        uploaded_by=w.admin,
        visibility=FileVisibility.GROUP_ONLY,
        group=w.group,
    )
    w.group_file_token = FileAccessTicket.issue(w.group_file, w.a)
    w.order = PrintOrder.objects.create(user=w.a)
    w.item = PrintOrderItem.objects.create(
        order=w.order, source_file=w.public_file, pages_count=1, original_file_name="p.pdf"
    )

    # chat
    w.message = Message.objects.create(
        group=w.group, sender=w.a, content="hello", attachment=ContentFile(b"%PDF-1.4 m", name="m.pdf")
    )

    # notifications, feedback
    w.notification = Notification.objects.create(user=w.a, title="Private", body="For A only")
    w.rating = AppFeedback.objects.create(
        user=w.a, kind=FeedbackKind.RATING, context=FeedbackContext.APP, rating=4, comment="private comment"
    )
    w.suggestion = AppFeedback.objects.create(
        user=w.a,
        kind=FeedbackKind.SUGGESTION,
        context=FeedbackContext.APP,
        title="Unpublished",
        suggestion="draft",
        status=FeedbackStatus.NEW,
    )

    # lecture + note
    w.lecture = Lecture.objects.create(
        subject=subject,
        title="Algorithms",
        original_file=ContentFile(b"%PDF-1.4", name="o.pdf"),
        original_filename="o.pdf",
        original_mime_type="application/pdf",
        original_size=8,
        original_sha256="a" * 64,
        viewer_pdf=ContentFile(b"%PDF-1.4", name="v.pdf"),
        status=LectureProcessingStatus.READY,
        page_count=1,
        is_published=True,
        uploaded_by=w.admin,
    )
    w.note = LectureNote.objects.create(student=w.a, lecture=w.lecture, content="A's private note")

    # verification, device, announcements
    w.verification = VerificationRequest.objects.create(
        user=w.a,
        student_profile=w.a.student_profile,
        university=university,
        faculty=faculty,
        major=major,
        academic_year=year,
        semester=semester,
        student_number="2150094",
        card_image=SimpleUploadedFile("card.png", PNG, content_type="image/png"),
    )
    w.installation = DeviceInstallation.objects.create(user=w.a, installation_id=uuid.uuid4(), platform="android")
    w.admins_only_announcement = Announcement.objects.create(
        title="Admins", description="x", created_by=w.admin, target_user_type=AnnouncementTargetUserType.ADMINS
    )
    w.other_major_announcement = Announcement.objects.create(
        title="Other major", description="x", created_by=w.admin, target_major=other_major
    )
    w.inactive_announcement = Announcement.objects.create(
        title="Inactive", description="x", created_by=w.admin, is_active=False
    )
    w.verified_announcement = Announcement.objects.create(
        title="Verified only",
        description="x",
        created_by=w.admin,
        target_user_type=AnnouncementTargetUserType.VERIFIED_STUDENTS,
    )
    return w


@dataclass(frozen=True)
class Case:
    """One object-level expectation. ``path`` receives the world; ``who`` names a world attribute (or ``anon``)."""

    label: str
    method: str
    path: Any
    who: str
    expected: tuple[int, ...]
    body: dict | None = None
    owner_ok: tuple[int, ...] | None = None  # positive control: the owner must get one of these


def _p(template: str):
    """Build a path from world attributes: ``_p('/support/tickets/{ticket.pk}/')``."""

    def build(w: World) -> str:
        def attr(match):
            obj: Any = w
            for part in match.group(1).split("."):
                obj = getattr(obj, part)
            return str(obj)

        return "/api/v1" + re.sub(r"\{([\w.]+)\}", attr, template)

    return build


NOT_VISIBLE = (403, 404)

IDOR_CASES: list[Case] = [
    # support
    Case("B reads A's ticket", "GET", _p("/support/tickets/{ticket.pk}/"), "b", (404,), owner_ok=(200,)),
    Case(
        "B posts into A's ticket", "POST", _p("/support/tickets/{ticket.pk}/messages/"), "b", (404,), {"message": "x"}
    ),
    Case(
        "B requests A's attachment ticket",
        "POST",
        _p("/support/messages/{ticket_message.pk}/attachment-ticket/"),
        "b",
        NOT_VISIBLE,
    ),
    Case(
        "B streams A's support attachment token",
        "GET",
        _p("/support/attachments/{support_token.token}/"),
        "b",
        (404,),
        owner_ok=(200,),
    ),
    # printing
    Case("B reads A's print order", "GET", _p("/printing/orders/my/{order.pk}/"), "b", (404,), owner_ok=(200,)),
    Case("B cancels A's print order", "POST", _p("/printing/orders/{order.pk}/cancel/"), "b", (404,)),
    Case("B requests A's print item ticket", "POST", _p("/printing/items/{item.pk}/access-ticket/"), "b", NOT_VISIBLE),
    # notifications
    Case("B reads A's notification", "GET", _p("/notifications/{notification.pk}/"), "b", (404,), owner_ok=(200,)),
    Case("B marks A's notification read", "POST", _p("/notifications/{notification.pk}/read/"), "b", (404,)),
    # feedback
    Case("B reads A's rating", "GET", _p("/feedback/mine/{rating.pk}/"), "b", (404,), owner_ok=(200,)),
    Case(
        "B files a privacy request on A's feedback",
        "POST",
        _p("/feedback/{rating.pk}/privacy-request/"),
        "b",
        (404,),
        {"action": "delete"},
    ),
    Case(
        "B reads A's unpublished suggestion publicly", "GET", _p("/feedback/suggestions/{suggestion.pk}/"), "b", (404,)
    ),
    Case("B votes on A's unpublished suggestion", "POST", _p("/feedback/{suggestion.pk}/vote/"), "b", (400, 404)),
    # lecture notes
    Case(
        "B reads A's lecture note", "GET", _p("/lectures/{lecture.pk}/notes/{note.pk}/"), "b", (404,), owner_ok=(200,)
    ),
    Case(
        "B edits A's lecture note",
        "PATCH",
        _p("/lectures/{lecture.pk}/notes/{note.pk}/"),
        "b",
        (404,),
        {"content": "pwned"},
    ),
    Case("B deletes A's lecture note", "DELETE", _p("/lectures/{lecture.pk}/notes/{note.pk}/"), "b", (404,)),
    # group chat: B is eligible for the curriculum but is not a member
    Case("non-member lists group messages", "GET", _p("/groups/{group.pk}/messages/"), "b", (403,), owner_ok=(200,)),
    Case(
        "non-member posts a group message", "POST", _p("/groups/{group.pk}/messages/"), "b", (403,), {"content": "hi"}
    ),
    Case(
        "non-member reads one group message",
        "GET",
        _p("/groups/{group.pk}/messages/{message.pk}/"),
        "b",
        (403,),
        owner_ok=(200,),
    ),
    Case("non-member deletes a group message", "DELETE", _p("/groups/{group.pk}/messages/{message.pk}/"), "b", (403,)),
    Case(
        "non-member deletes via /delete/", "DELETE", _p("/groups/{group.pk}/messages/{message.pk}/delete/"), "b", (403,)
    ),
    Case(
        "non-member reports a group message",
        "POST",
        _p("/groups/{group.pk}/messages/{message.pk}/report/"),
        "b",
        (403,),
        {"reason": "x"},
    ),
    Case(
        "non-member requests a chat attachment ticket",
        "POST",
        _p("/groups/{group.pk}/messages/{message.pk}/attachment-ticket/"),
        "b",
        (403,),
    ),
    Case("non-member requests a chat ws ticket", "POST", _p("/groups/{group.pk}/chat-ticket/"), "b", (403,)),
    Case("normal user lists group messages", "GET", _p("/groups/{group.pk}/messages/"), "normal", (403,)),
    Case("non-member lists group files", "GET", _p("/groups/{group.pk}/files/"), "b", (403,), owner_ok=(200,)),
    # files visibility
    Case("B reads a group-only file", "GET", _p("/files/{group_file.pk}/"), "b", (404, 403), owner_ok=(200,)),
    Case(
        "B requests a ticket for a group-only file",
        "POST",
        _p("/files/{group_file.pk}/access-ticket/"),
        "b",
        NOT_VISIBLE,
    ),
    Case(
        "B streams A's file token",
        "GET",
        _p("/protected-files/{group_file_token.token}/"),
        "b",
        NOT_VISIBLE,
        owner_ok=(200,),
    ),
    Case("B reads an admin-only file", "GET", _p("/files/{admin_file.pk}/"), "b", NOT_VISIBLE),
    Case("normal user reads an admin-only file", "GET", _p("/files/{admin_file.pk}/"), "normal", NOT_VISIBLE),
    Case(
        "B requests a ticket for an admin-only file",
        "POST",
        _p("/files/{admin_file.pk}/access-ticket/"),
        "b",
        NOT_VISIBLE,
    ),
    # verification
    Case(
        "student cannot read the card via the dashboard ticket",
        "POST",
        _p("/dashboard/verifications/{verification.pk}/card-ticket/"),
        "b",
        (403,),
    ),
    Case(
        "student cannot approve verification",
        "POST",
        _p("/dashboard/verifications/{verification.pk}/approve/"),
        "a",
        (403,),
    ),
    Case(
        "student cannot read the dashboard verification detail",
        "GET",
        _p("/dashboard/verifications/{verification.pk}/"),
        "b",
        (403,),
    ),
    # devices
    Case(
        "B patches A's device",
        "PATCH",
        _p("/mobile/devices/{installation.installation_id}/"),
        "b",
        (404, 403),
        {"app_version": "9"},
    ),
    Case("B revokes A's device", "POST", _p("/mobile/devices/{installation.installation_id}/revoke/"), "b", (404, 403)),
    # announcements: targeting is enforced on the detail route too
    Case(
        "student reads an admins-only announcement",
        "GET",
        _p("/announcements/{admins_only_announcement.pk}/"),
        "b",
        (404,),
    ),
    Case(
        "student reads another major's announcement",
        "GET",
        _p("/announcements/{other_major_announcement.pk}/"),
        "b",
        (404,),
    ),
    Case(
        "student reads an inactive announcement", "GET", _p("/announcements/{inactive_announcement.pk}/"), "b", (404,)
    ),
    Case(
        "normal user reads a verified-students announcement",
        "GET",
        _p("/announcements/{verified_announcement.pk}/"),
        "normal",
        (404,),
    ),
    # anonymous, object routes
    Case("anonymous reads a ticket", "GET", _p("/support/tickets/{ticket.pk}/"), "anon", (401,)),
    Case("anonymous reads a file", "GET", _p("/files/{public_file.pk}/"), "anon", (401,)),
    Case("anonymous reads group messages", "GET", _p("/groups/{group.pk}/messages/"), "anon", (401,)),
]


@pytest.mark.parametrize("case", IDOR_CASES, ids=[c.label for c in IDOR_CASES])
@pytest.mark.django_db
def test_object_level_authorization(world: World, case: Case):
    actor = None if case.who == "anon" else getattr(world, case.who)
    client = _client_for(actor)
    url = case.path(world)
    kwargs: dict[str, Any] = {}
    if case.body is not None:
        kwargs = {"data": case.body, "format": "json"}
    response = getattr(client, case.method.lower())(url, **kwargs)
    assert response.status_code in case.expected, (
        f"{case.label}: {case.method} {url} as {case.who} -> {response.status_code}, expected {case.expected}: "
        f"{getattr(response, 'data', response.content)!r}"
    )
    if case.owner_ok is not None:
        owner = _client_for(world.a)
        positive = getattr(owner, case.method.lower())(url, **kwargs)
        assert positive.status_code in case.owner_ok, (
            f"positive control failed: owner {case.method} {url} -> {positive.status_code}, expected {case.owner_ok}"
        )


@pytest.mark.django_db
def test_idor_attempts_did_not_change_anyones_data(world: World):
    """State-changing attempts by B above must also leave A's data intact (checked after replaying them)."""
    client = _client_for(world.b)
    for case in IDOR_CASES:
        if case.who != "b" or case.method == "GET":
            continue
        kwargs: dict[str, Any] = {}
        if case.body is not None:
            kwargs = {"data": case.body, "format": "json"}
        getattr(client, case.method.lower())(case.path(world), **kwargs)
    world.note.refresh_from_db()
    assert world.note.content == "A's private note" and not world.note.is_deleted
    world.order.refresh_from_db()
    assert world.order.status == PrintOrder._meta.get_field("status").default
    world.notification.refresh_from_db()
    assert not world.notification.is_read
    world.rating.refresh_from_db()
    assert not world.rating.is_hidden and world.rating.deletion_requested_at is None
    world.installation.refresh_from_db()
    assert world.installation.revoked_at is None and world.installation.app_version == ""
    assert not world.group.messages.exclude(pk=world.message.pk).exists()
    assert not world.group.messages.get(pk=world.message.pk).is_deleted
    assert not world.suggestion.votes.exists()


@pytest.mark.django_db
def test_verification_me_only_ever_returns_the_callers_own_request(world: World):
    response = _client_for(world.b).get("/api/v1/verification/me/")
    assert response.status_code == 200
    assert str(world.verification.pk) not in str(response.data.get("data", {}).get("id", ""))
    own = _client_for(world.a).get("/api/v1/verification/me/")
    assert own.data["data"]["id"] == world.verification.pk
