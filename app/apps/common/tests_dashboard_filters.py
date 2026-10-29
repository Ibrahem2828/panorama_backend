"""Dashboard list filters: subjects, groups, chat groups, verifications, enrollment requests, files and lectures.

Each endpoint gets: every filter, a combination, invalid input -> 400, a permission negative and a query-count check
that proves the page costs the same number of queries whatever its size (no N+1).
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.chat.models import Message, MessageReport, MessageReportStatus
from apps.enrollment.models import EnrollmentRequest, EnrollmentStatus
from apps.files.models import FileProcessingStatus, FileResource, FileVisibility
from apps.groups.models import (
    ExternalChannelType,
    Group,
    GroupExternalChannel,
    GroupMembership,
    GroupMembershipStatus,
)
from apps.lectures.models import Lecture, LectureProcessingStatus
from apps.universities.models import AcademicYear, Faculty, Major, Semester, Subject, University
from apps.verification.models import VerificationRequest, VerificationStatus

pytestmark = pytest.mark.django_db

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _user(n: int, role: str = UserRole.STUDENT, **extra) -> User:
    return User.objects.create_user(
        full_name=extra.pop("full_name", f"Person {n}"),
        email=f"f{n}@filters.test",
        phone_number=f"+9639660{n:04d}",
        password="StrongPass123!",
        role=role,
        **extra,
    )


def _set_created(model, pk: int, when) -> None:
    model.objects.filter(pk=pk).update(created_at=when)


def _client(user: User) -> APIClient:
    c = APIClient()
    c.force_authenticate(user)
    return c


def _results(response) -> list[dict]:
    assert response.status_code == 200, response.content
    data = response.data["data"]
    return data["results"] if isinstance(data, dict) and "results" in data else data


def _names(response, key: str = "name") -> list[str]:
    return [row[key] for row in _results(response)]


def _count_queries(client: APIClient, url: str) -> int:
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(url)
    assert response.status_code == 200, response.content
    return len(ctx)


@pytest.fixture
def w():
    w = SimpleNamespace()
    w.now = timezone.now()
    w.uni = University.objects.create(name="Private University", code="PU")
    w.uni2 = University.objects.create(name="Other University", code="OU")
    w.fac = Faculty.objects.create(university=w.uni, name="Informatics", code="4")
    w.fac2 = Faculty.objects.create(university=w.uni, name="Pharmacy", code="3")
    w.fac_other = Faculty.objects.create(university=w.uni2, name="Law", code="L")
    w.major = Major.objects.create(faculty=w.fac, name="Software", code="SE")
    w.y1 = AcademicYear.objects.create(name="Year 1", order=1)
    w.y2 = AcademicYear.objects.create(name="Year 2", order=2)
    w.s1 = Semester.objects.create(name="Semester 1", order=1)
    w.s2 = Semester.objects.create(name="Semester 2", order=2)
    w.admin = _user(1, UserRole.ADMIN, full_name="Admin")
    w.content = _user(2, UserRole.CONTENT_MANAGER, full_name="Content")
    w.print_staff = _user(3, UserRole.PRINT_STAFF, full_name="Printer")
    w.student = _user(4, full_name="Student")
    StudentProfile.objects.create(user=w.student, verification_status=StudentVerificationStatus.APPROVED)
    w.student2 = _user(5, full_name="Student Two")
    StudentProfile.objects.create(user=w.student2, verification_status=StudentVerificationStatus.PENDING)
    return w


# ---------------------------------------------------------------------------------------------------------------------
# subjects
# ---------------------------------------------------------------------------------------------------------------------

SUBJECTS = "/api/v1/dashboard/subjects/"


@pytest.fixture
def subjects(w):
    w.prog = Subject.objects.create(faculty=w.fac, academic_year=w.y1, semester=w.s1, name="Programming", code="P1")
    w.arch = Subject.objects.create(
        faculty=w.fac, major=w.major, academic_year=w.y2, semester=w.s2, name="Architecture", code="A2"
    )
    w.chem = Subject.objects.create(
        faculty=w.fac2, academic_year=w.y1, semester=w.s1, name="Chemistry", code="C1", is_active=False
    )
    w.law = Subject.objects.create(faculty=w.fac_other, academic_year=w.y2, semester=w.s1, name="Civil Law", code="L1")
    # content: prog has 2 groups (one deleted -> not counted), 1 lecture, 2 files; arch has 1 group; chem nothing
    Group.objects.create(name="G-prog", university=w.uni, faculty=w.fac, subject=w.prog, created_by=w.admin)
    Group.objects.create(
        name="G-prog-old", university=w.uni, faculty=w.fac, subject=w.prog, created_by=w.admin, is_deleted=True
    )
    Group.objects.create(name="G-arch", university=w.uni, faculty=w.fac, subject=w.arch, created_by=w.admin)
    w.lecture = _lecture(w, subject=w.prog, sha="a" * 64)
    _file(w, "F1", subject=w.prog)
    _file(w, "F2", subject=w.prog)
    _file(w, "F-deleted", subject=w.prog, is_deleted=True)
    return w


def _lecture(w, *, subject=None, sha="b" * 64, title="Lec", status=LectureProcessingStatus.READY, **extra) -> Lecture:
    return Lecture.objects.create(
        subject=subject,
        title=title,
        original_file="lectures/originals/x.pdf",
        original_filename="x.pdf",
        original_mime_type="application/pdf",
        original_size=8,
        original_sha256=sha,
        status=status,
        uploaded_by=w.admin,
        **extra,
    )


def _file(w, title, **extra) -> FileResource:
    return FileResource.objects.create(
        title=title, file=ContentFile(b"%PDF-1.4", name=f"{title}.pdf"), uploaded_by=w.admin, **extra
    )


def test_subjects_row_gains_counts_and_keeps_existing_fields(w, subjects):
    rows = {r["name"]: r for r in _results(_client(w.admin).get(SUBJECTS))}
    prog = rows["Programming"]
    assert (prog["groups_count"], prog["lectures_count"], prog["files_count"]) == (1, 1, 2)
    assert rows["Chemistry"]["groups_count"] == rows["Chemistry"]["files_count"] == 0
    for existing in ("faculty_name", "major_name", "academic_year_name", "semester_name", "faculty_detail", "code"):
        assert existing in prog
    assert prog["faculty_name"] == "Informatics" and prog["academic_year_name"] == "Year 1"
    assert rows["Architecture"]["major_name"] == "Software"


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("faculty={fac}", {"Programming", "Architecture"}),
        ("major={major}", {"Architecture"}),
        ("has_major=false", {"Programming", "Chemistry", "Civil Law"}),
        ("university={uni}", {"Programming", "Architecture", "Chemistry"}),
        ("university={uni2}", {"Civil Law"}),
        ("academic_year={y2}", {"Architecture", "Civil Law"}),
        ("semester={s2}", {"Architecture"}),
        ("is_active=false", {"Chemistry"}),
        ("is_active=true", {"Programming", "Architecture", "Civil Law"}),
        ("has_groups=true", {"Programming", "Architecture"}),
        ("has_groups=false", {"Chemistry", "Civil Law"}),
        ("has_lectures=true", {"Programming"}),
        ("has_lectures=false", {"Architecture", "Chemistry", "Civil Law"}),
        ("has_files=true", {"Programming"}),
        ("has_files=false", {"Architecture", "Chemistry", "Civil Law"}),
        ("search=chem", {"Chemistry"}),
        ("search=a2", {"Architecture"}),
        ("university={uni}&has_groups=false", {"Chemistry"}),
        ("faculty={fac}&semester={s1}&has_files=true", {"Programming"}),
    ],
)
def test_subject_filters(w, subjects, query, expected):
    ids = {
        "fac": w.fac.pk,
        "major": w.major.pk,
        "uni": w.uni.pk,
        "uni2": w.uni2.pk,
        "y2": w.y2.pk,
        "s1": w.s1.pk,
        "s2": w.s2.pk,
    }
    assert set(_names(_client(w.admin).get(f"{SUBJECTS}?{query.format(**ids)}"))) == expected


@pytest.mark.parametrize(
    ("ordering", "first"),
    [
        ("name", "Architecture"),
        ("-name", "Programming"),
        ("code", "A2"),
        ("-created_at", "Civil Law"),
        ("-groups_count", None),
        ("groups_count", None),
    ],
)
def test_subject_ordering(w, subjects, ordering, first):
    rows = _results(_client(w.admin).get(f"{SUBJECTS}?ordering={ordering}"))
    if ordering == "code":
        assert rows[0]["code"] == first
    elif "groups_count" in ordering:
        counts = [r["groups_count"] for r in rows]
        assert counts == sorted(counts, reverse=ordering.startswith("-"))
    else:
        assert rows[0]["name"] == first


@pytest.mark.parametrize(
    "query",
    ["has_groups=maybe", "has_lectures=2", "has_files=x", "university=abc", "faculty=abc", "is_active=zzz"],
)
def test_subject_invalid_filters_400(w, subjects, query):
    assert _client(w.admin).get(f"{SUBJECTS}?{query}").status_code == 400


def test_subjects_permission_negatives(w, subjects):
    assert APIClient().get(SUBJECTS).status_code == 401
    assert _client(w.print_staff).get(SUBJECTS).status_code == 403
    assert _client(w.student).get(SUBJECTS).status_code == 403


def test_subject_query_count_constant(w, subjects):
    c = _client(w.admin)
    small = _count_queries(c, f"{SUBJECTS}?page_size=100")
    for i in range(25):
        subject = Subject.objects.create(faculty=w.fac, academic_year=w.y1, semester=w.s2, name=f"Extra {i}")
        Group.objects.create(name=f"EG{i}", university=w.uni, faculty=w.fac, subject=subject, created_by=w.admin)
        _file(w, f"EF{i}", subject=subject)
    assert _count_queries(c, f"{SUBJECTS}?page_size=100") == small


def test_subject_create_still_works_and_reports_counts(w):
    response = _client(w.admin).post(
        SUBJECTS,
        {
            "faculty": w.fac.pk,
            "academic_year": w.y1.pk,
            "semester": w.s1.pk,
            "name": "Brand new",
            "code": "bn1",
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    data = response.data["data"]
    assert (data["groups_count"], data["lectures_count"], data["files_count"]) == (0, 0, 0)


# ---------------------------------------------------------------------------------------------------------------------
# groups
# ---------------------------------------------------------------------------------------------------------------------

GROUPS = "/api/v1/dashboard/groups/"


@pytest.fixture
def groups(w, subjects):
    c = w.now
    w.g_subject = Group.objects.get(name="G-prog")
    w.g_faculty = Group.objects.create(
        name="Faculty hall", university=w.uni, faculty=w.fac, created_by=w.admin, requires_approval=False
    )
    w.g_general = Group.objects.create(
        name="Everyone", university=w.uni, created_by=w.admin, send_messages_permission="admins_only", is_active=False
    )
    for user in (w.student, w.student2):
        GroupMembership.objects.create(group=w.g_subject, user=user, status=GroupMembershipStatus.APPROVED)
    GroupMembership.objects.create(group=w.g_faculty, user=w.student, status=GroupMembershipStatus.APPROVED)
    GroupMembership.objects.create(group=w.g_faculty, user=w.student2, status=GroupMembershipStatus.PENDING)
    pending_only = _user(40)
    GroupMembership.objects.create(group=w.g_general, user=pending_only, status=GroupMembershipStatus.PENDING)
    GroupMembership.objects.create(
        group=w.g_general, user=w.student, status=GroupMembershipStatus.APPROVED, is_deleted=True
    )
    GroupExternalChannel.objects.create(
        group=w.g_subject, channel_type=ExternalChannelType.WHATSAPP, encrypted_url="x", is_active=True
    )
    GroupExternalChannel.objects.create(
        group=w.g_faculty, channel_type=ExternalChannelType.WHATSAPP, encrypted_url="x", is_active=False
    )
    _set_created(Group, w.g_subject.pk, c - timedelta(days=30))
    _set_created(Group, w.g_faculty.pk, c - timedelta(days=10))
    _set_created(Group, w.g_general.pk, c - timedelta(days=1))
    return w


def _group_names(response) -> set[str]:
    return {n for n in _names(response) if n in {"G-prog", "G-arch", "Faculty hall", "Everyone"}}


def test_group_rows(w, groups):
    rows = {r["name"]: r for r in _results(_client(w.admin).get(GROUPS))}
    subject, faculty, general = rows["G-prog"], rows["Faculty hall"], rows["Everyone"]
    assert (subject["kind"], faculty["kind"], general["kind"]) == ("subject", "faculty", "general")
    assert (subject["members_count"], subject["pending_requests_count"], subject["has_whatsapp"]) == (2, 0, True)
    assert (faculty["members_count"], faculty["pending_requests_count"], faculty["has_whatsapp"]) == (1, 1, False)
    # a deleted approved membership is not a member; a pending one is a request
    assert (general["members_count"], general["pending_requests_count"]) == (0, 1)
    assert subject["has_whatsapp_channel"] is True and "send_messages_permission" in subject


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("kind=subject", {"G-prog", "G-arch"}),
        ("kind=faculty", {"Faculty hall"}),
        ("kind=general", {"Everyone"}),
        ("requires_approval=false", {"Faculty hall"}),
        ("requires_approval=true", {"G-prog", "G-arch", "Everyone"}),
        ("has_whatsapp=true", {"G-prog"}),  # an inactive channel does not count
        ("has_whatsapp=false", {"G-arch", "Faculty hall", "Everyone"}),
        ("send_messages_permission=admins_only", {"Everyone"}),
        ("send_messages_permission=all_members", {"G-prog", "G-arch", "Faculty hall"}),
        ("has_pending_requests=true", {"Faculty hall", "Everyone"}),
        ("has_pending_requests=false", {"G-prog", "G-arch"}),
        ("members_min=2", {"G-prog"}),
        ("members_max=1", {"G-arch", "Faculty hall", "Everyone"}),
        ("members_min=1&members_max=1", {"Faculty hall"}),
        ("created_after=D20", {"Faculty hall", "Everyone", "G-arch"}),
        ("created_before=D20", {"G-prog"}),
        ("is_active=false", {"Everyone"}),
        ("university={uni}&kind=subject&has_whatsapp=true", {"G-prog"}),
        ("faculty={fac}", {"G-prog", "G-arch", "Faculty hall"}),
        ("search=hall", {"Faculty hall"}),
    ],
)
def test_group_filters(w, groups, query, expected):
    d20 = (w.now - timedelta(days=20)).date().isoformat()
    query = query.replace("D20", d20).replace("{uni}", str(w.uni.pk)).replace("{fac}", str(w.fac.pk))
    response = _client(w.admin).get(f"{GROUPS}?{query}&page_size=100")
    assert _group_names(response) == expected


def test_group_subject_filter_still_works(w, groups):
    response = _client(w.admin).get(f"{GROUPS}?subject={w.prog.pk}")
    assert _group_names(response) == {"G-prog"}


@pytest.mark.parametrize(
    ("ordering", "key", "reverse"),
    [
        ("name", "name", False),
        ("-name", "name", True),
        ("members_count", "members_count", False),
        ("-members_count", "members_count", True),
        ("pending_requests_count", "pending_requests_count", False),
        ("-pending_requests_count", "pending_requests_count", True),
    ],
)
def test_group_ordering(w, groups, ordering, key, reverse):
    rows = _results(_client(w.admin).get(f"{GROUPS}?ordering={ordering}&page_size=100"))
    values = [r[key] for r in rows]
    assert values == sorted(values, reverse=reverse)


def test_group_ordering_by_created_at(w, groups):
    rows = _results(_client(w.admin).get(f"{GROUPS}?ordering=-created_at&kind=faculty"))
    assert [r["name"] for r in rows] == ["Faculty hall"]
    names = [r["name"] for r in _results(_client(w.admin).get(f"{GROUPS}?ordering=created_at&page_size=100"))]
    assert names.index("G-prog") < names.index("Faculty hall") < names.index("Everyone")


@pytest.mark.parametrize(
    "query",
    [
        "kind=other",
        "requires_approval=maybe",
        "has_whatsapp=x",
        "has_pending_requests=2",
        "send_messages_permission=nobody",
        "members_min=abc",
        "members_max=-1",
        "created_after=2026-02-30",
        "created_before=tomorrow",
    ],
)
def test_group_invalid_filters_400(w, groups, query):
    assert _client(w.admin).get(f"{GROUPS}?{query}").status_code == 400


def test_group_permission_negatives(w, groups):
    assert APIClient().get(GROUPS).status_code == 401
    assert _client(w.print_staff).get(GROUPS).status_code == 403
    assert _client(w.student).get(GROUPS).status_code == 403
    assert _client(w.content).get(GROUPS).status_code == 200  # content managers hold groups.manage


def test_group_query_count_constant(w, groups):
    c = _client(w.admin)
    small = _count_queries(c, f"{GROUPS}?page_size=100")
    for i in range(25):
        group = Group.objects.create(name=f"Bulk {i}", university=w.uni, created_by=w.admin)
        GroupMembership.objects.create(group=group, user=w.student, status=GroupMembershipStatus.APPROVED)
        GroupMembership.objects.create(group=group, user=w.student2, status=GroupMembershipStatus.PENDING)
        GroupExternalChannel.objects.create(group=group, channel_type=ExternalChannelType.WHATSAPP, encrypted_url="x")
    assert _count_queries(c, f"{GROUPS}?page_size=100") == small


# ---------------------------------------------------------------------------------------------------------------------
# chat groups
# ---------------------------------------------------------------------------------------------------------------------

CHAT = "/api/v1/dashboard/chat/groups/"


@pytest.fixture
def chats(w, groups):
    c = w.now
    w.g_arch = Group.objects.get(name="G-arch")
    Group.objects.filter(pk=w.g_arch.pk).update(major=w.major, academic_year=w.y2, semester=w.s2)
    Group.objects.filter(pk=w.g_subject.pk).update(academic_year=w.y1, semester=w.s1)
    # messages: subject group active 2 days ago (+ a deleted newer one that must not count); faculty hall 40 days ago
    m1 = Message.objects.create(group=w.g_subject, sender=w.student, content="hello")
    m1b = Message.objects.create(group=w.g_subject, sender=w.student, content="deleted newer", is_deleted=True)
    m2 = Message.objects.create(group=w.g_faculty, sender=w.student, content="old")
    Message.objects.filter(pk=m1.pk).update(created_at=c - timedelta(days=2))
    Message.objects.filter(pk=m1b.pk).update(created_at=c - timedelta(hours=1))
    Message.objects.filter(pk=m2.pk).update(created_at=c - timedelta(days=40))
    # reports: two open on subject group (one legacy "reviewed" still unresolved), one dismissed on faculty hall
    MessageReport.objects.create(message=m1, reported_by=w.student, reason="spam")
    MessageReport.objects.create(message=m1, reported_by=w.student2, reason="rude", status=MessageReportStatus.REVIEWED)
    MessageReport.objects.create(message=m2, reported_by=w.student, reason="x", status=MessageReportStatus.DISMISSED)
    return w


def _chat_names(response) -> list[str]:
    return [n for n in _names(response) if n in {"G-prog", "G-arch", "Faculty hall", "Everyone"}]


def test_chat_group_rows(w, chats):
    rows = {r["name"]: r for r in _results(_client(w.admin).get(CHAT))}
    subject = rows["G-prog"]
    assert subject["kind"] == "subject" and subject["open_reports"] == 2 and subject["members"] == 2
    assert subject["last_message_excerpt"] == "hello"  # deleted messages never count
    assert subject["last_message_at"] is not None
    assert rows["Faculty hall"]["kind"] == "faculty" and rows["Faculty hall"]["open_reports"] == 0
    assert rows["Everyone"]["kind"] == "general" and rows["Everyone"]["last_message_at"] is None
    assert {"subject_name", "faculty_name", "is_active"} <= set(subject)


def test_chat_default_order_reported_first(w, chats):
    assert _chat_names(_client(w.admin).get(CHAT))[0] == "G-prog"


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("faculty={fac}", {"G-prog", "G-arch", "Faculty hall"}),
        ("major={major}", {"G-arch"}),
        ("academic_year={y2}", {"G-arch"}),
        ("semester={s1}", {"G-prog"}),
        ("subject={prog}", {"G-prog"}),
        ("kind=subject", {"G-prog", "G-arch"}),
        ("kind=faculty", {"Faculty hall"}),
        ("kind=general", {"Everyone"}),
        ("has_messages=true", {"G-prog", "Faculty hall"}),
        ("has_messages=false", {"G-arch", "Everyone"}),
        ("active_after=D10", {"G-prog"}),
        ("active_after=D60", {"G-prog", "Faculty hall"}),
        ("has_open_reports=true", {"G-prog"}),
        ("has_open_reports=false", {"G-arch", "Faculty hall", "Everyone"}),
        ("reported=1", {"G-prog"}),
        ("reported=true", {"G-prog"}),
        ("reported_only=true", {"G-prog"}),
        ("reported_only=false", {"G-prog", "G-arch", "Faculty hall", "Everyone"}),
        ("search=hall", {"Faculty hall"}),
        ("search=Programming", {"G-prog"}),
        ("kind=subject&has_messages=true", {"G-prog"}),
        ("faculty={fac}&has_open_reports=false&has_messages=true", {"Faculty hall"}),
    ],
)
def test_chat_filters(w, chats, query, expected):
    replacements = {
        "{fac}": w.fac.pk,
        "{major}": w.major.pk,
        "{y2}": w.y2.pk,
        "{s1}": w.s1.pk,
        "{prog}": w.prog.pk,
        "D10": (w.now - timedelta(days=10)).date().isoformat(),
        "D60": (w.now - timedelta(days=60)).date().isoformat(),
    }
    for key, value in replacements.items():
        query = query.replace(key, str(value))
    assert set(_chat_names(_client(w.admin).get(f"{CHAT}?{query}&page_size=100"))) == expected


@pytest.mark.parametrize(
    ("ordering", "expected_first_two"),
    [
        ("-last_message_at", ["G-prog", "Faculty hall"]),
        ("last_message_at", ["Faculty hall", "G-prog"]),  # groups with no message stay last
        ("-open_reports", ["G-prog"]),
        ("-members", ["G-prog", "Faculty hall"]),
    ],
)
def test_chat_ordering(w, chats, ordering, expected_first_two):
    names = _chat_names(_client(w.admin).get(f"{CHAT}?ordering={ordering}&page_size=100"))
    assert names[: len(expected_first_two)] == expected_first_two
    if ordering == "last_message_at":
        assert set(names[-2:]) == {"G-arch", "Everyone"}


def test_chat_open_reports_ascending_puts_clean_groups_first(w, chats):
    names = _chat_names(_client(w.admin).get(f"{CHAT}?ordering=open_reports&page_size=100"))
    assert names[-1] == "G-prog"


@pytest.mark.parametrize(
    "query",
    [
        "kind=weird",
        "has_messages=maybe",
        "has_open_reports=2",
        "reported=perhaps",
        "active_after=2026-99-01",
        "active_after=last-week",
        "faculty=abc",
        "ordering=secret",
    ],
)
def test_chat_invalid_filters_400(w, chats, query):
    assert _client(w.admin).get(f"{CHAT}?{query}").status_code == 400


def test_chat_permission_negatives(w, chats):
    assert APIClient().get(CHAT).status_code == 401
    assert _client(w.print_staff).get(CHAT).status_code == 403
    assert _client(w.student).get(CHAT).status_code == 403


def test_chat_query_count_constant(w, chats):
    c = _client(w.admin)
    small = _count_queries(c, f"{CHAT}?page_size=100")
    for i in range(25):
        group = Group.objects.create(name=f"Chat {i}", university=w.uni, faculty=w.fac, created_by=w.admin)
        GroupMembership.objects.create(group=group, user=w.student, status=GroupMembershipStatus.APPROVED)
        message = Message.objects.create(group=group, sender=w.student, content="x")
        MessageReport.objects.create(message=message, reported_by=w.student2, reason="r")
    assert _count_queries(c, f"{CHAT}?page_size=100") == small


# ---------------------------------------------------------------------------------------------------------------------
# verification requests
# ---------------------------------------------------------------------------------------------------------------------

VERIFICATIONS = "/api/v1/dashboard/verifications/"


def _verification(w, user, *, semester, number, status=VerificationStatus.PENDING, reviewer=None):
    profile = user.student_profile
    return VerificationRequest.objects.create(
        user=user,
        student_profile=profile,
        university=w.uni,
        faculty=w.fac,
        academic_year=w.y1,
        semester=semester,
        student_number=number,
        card_image=SimpleUploadedFile("c.png", PNG, content_type="image/png"),
        status=status,
        reviewed_by=reviewer,
    )


@pytest.fixture
def verifications(w):
    w.v1 = _verification(w, w.student, semester=w.s1, number="2150001")
    w.v2 = _verification(
        w, w.student2, semester=w.s2, number="2150002", status=VerificationStatus.APPROVED, reviewer=w.admin
    )
    _set_created(VerificationRequest, w.v1.pk, w.now - timedelta(days=20))
    _set_created(VerificationRequest, w.v2.pk, w.now - timedelta(days=3))
    return w


def test_verification_filters(w, verifications):
    c = _client(w.admin)

    def ids(query):
        return {r["id"] for r in _results(c.get(f"{VERIFICATIONS}?{query}"))}

    d10 = (w.now - timedelta(days=10)).date().isoformat()
    assert ids(f"semester={w.s1.pk}") == {w.v1.pk}
    assert ids(f"semester={w.s2.pk}") == {w.v2.pk}
    assert ids(f"reviewed_by={w.admin.pk}") == {w.v2.pk}
    assert ids(f"created_after={d10}") == {w.v2.pk}
    assert ids(f"created_before={d10}") == {w.v1.pk}
    assert ids(f"created_after={d10}&created_before={d10}") == set()
    assert ids(f"status=pending&created_before={d10}") == {w.v1.pk}
    assert ids("status=approved") == {w.v2.pk}  # existing filters keep working
    assert ids(f"faculty={w.fac.pk}&academic_year={w.y1.pk}") == {w.v1.pk, w.v2.pk}
    assert ids("search=2150002") == {w.v2.pk}


@pytest.mark.parametrize(
    "query", ["created_after=nope", "created_before=2026-00-10", "semester=abc", "reviewed_by=x", "status=zzz"]
)
def test_verification_invalid_filters_400(w, verifications, query):
    assert _client(w.admin).get(f"{VERIFICATIONS}?{query}").status_code == 400


def test_verification_permissions_and_query_count(w, verifications):
    assert APIClient().get(VERIFICATIONS).status_code == 401
    assert _client(w.print_staff).get(VERIFICATIONS).status_code == 403
    assert _client(w.content).get(VERIFICATIONS).status_code == 403
    c = _client(w.admin)
    small = _count_queries(c, f"{VERIFICATIONS}?page_size=100")
    for i in range(15):
        user = _user(100 + i)
        StudentProfile.objects.create(user=user)
        _verification(w, user, semester=w.s1, number=f"9{i:06d}")
    assert _count_queries(c, f"{VERIFICATIONS}?page_size=100") == small


# ---------------------------------------------------------------------------------------------------------------------
# enrollment requests
# ---------------------------------------------------------------------------------------------------------------------

ENROLLMENTS = "/api/v1/dashboard/enrollment/requests/"


def _enrollment(w, user, *, faculty, year=None, semester=None, status=EnrollmentStatus.PENDING):
    return EnrollmentRequest.objects.create(
        user=user,
        student_profile=user.student_profile,
        faculty=faculty,
        academic_year=year,
        semester=semester,
        screenshot="enrollment_screenshots/x.png",
        status=status,
    )


@pytest.fixture
def enrollments(w):
    StudentProfile.objects.filter(user=w.student).update(student_number="2150001")
    StudentProfile.objects.filter(user=w.student2).update(student_number="2150002")
    w.e1 = _enrollment(w, w.student, faculty=w.fac, year=w.y1, semester=w.s1)
    w.e2 = _enrollment(w, w.student2, faculty=w.fac2, year=w.y2, semester=w.s2, status=EnrollmentStatus.APPROVED)
    w.e3 = _enrollment(w, w.student, faculty=w.fac, year=w.y1, semester=w.s1, status=EnrollmentStatus.REJECTED)
    _set_created(EnrollmentRequest, w.e1.pk, w.now - timedelta(days=2))
    _set_created(EnrollmentRequest, w.e2.pk, w.now - timedelta(days=12))
    _set_created(EnrollmentRequest, w.e3.pk, w.now - timedelta(days=30))
    return w


def test_enrollment_filters(w, enrollments):
    c = _client(w.admin)

    def ids(query):
        return {r["id"] for r in _results(c.get(f"{ENROLLMENTS}?{query}"))}

    d20 = (w.now - timedelta(days=20)).date().isoformat()
    assert ids("status=pending") == {w.e1.pk}
    assert ids("status=approved") == {w.e2.pk}
    assert ids(f"faculty={w.fac2.pk}") == {w.e2.pk}
    assert ids(f"academic_year={w.y1.pk}") == {w.e1.pk, w.e3.pk}
    assert ids(f"semester={w.s2.pk}") == {w.e2.pk}
    assert ids(f"created_after={d20}") == {w.e1.pk, w.e2.pk}
    assert ids(f"created_before={d20}") == {w.e3.pk}
    assert ids("search=Student Two".replace(" ", "%20")) == {w.e2.pk}
    assert ids("search=2150001") == {w.e1.pk, w.e3.pk}
    assert ids(f"search=2150001&status=rejected&faculty={w.fac.pk}") == {w.e3.pk}
    assert ids("") == {w.e1.pk, w.e2.pk, w.e3.pk}


@pytest.mark.parametrize(
    "query",
    [
        "status=nonsense",
        "faculty=abc",
        "academic_year=x",
        "semester=y",
        "created_after=bad",
        "created_before=2026-1-1x",
    ],
)
def test_enrollment_invalid_filters_400(w, enrollments, query):
    assert _client(w.admin).get(f"{ENROLLMENTS}?{query}").status_code == 400


def test_enrollment_permissions_and_query_count(w, enrollments):
    assert APIClient().get(ENROLLMENTS).status_code == 401
    assert _client(w.print_staff).get(ENROLLMENTS).status_code == 403
    assert _client(w.student).get(ENROLLMENTS).status_code == 403
    c = _client(w.admin)
    w.e1.items.create(subject=Subject.objects.create(faculty=w.fac, academic_year=w.y1, semester=w.s1, name="Seed"))
    small = _count_queries(c, f"{ENROLLMENTS}?page_size=100")
    for i in range(15):
        user = _user(200 + i)
        StudentProfile.objects.create(user=user, student_number=f"8{i:06d}")
        item_request = _enrollment(w, user, faculty=w.fac, year=w.y1, semester=w.s1)
        item_request.items.create(
            subject=Subject.objects.create(faculty=w.fac, academic_year=w.y1, semester=w.s1, name=f"S{i}")
        )
    assert _count_queries(c, f"{ENROLLMENTS}?page_size=100") == small


# ---------------------------------------------------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------------------------------------------------

FILES = "/api/v1/dashboard/files/"


@pytest.fixture
def files(w, subjects):
    w.group = Group.objects.get(name="G-prog")
    other = _user(60, UserRole.CONTENT_MANAGER, full_name="Uploader Two")
    w.uploader2 = other
    w.f_pub = FileResource.objects.get(title="F1")
    w.f_grp = FileResource.objects.create(
        title="Group notes",
        file=ContentFile(b"%PDF-1.4", name="g.pdf"),
        uploaded_by=other,
        visibility=FileVisibility.GROUP_ONLY,
        group=w.group,
        processing_status=FileProcessingStatus.READY,
    )
    w.f_adm = FileResource.objects.create(
        title="Admin memo",
        file=ContentFile(b"%PDF-1.4", name="a.pdf"),
        uploaded_by=other,
        visibility=FileVisibility.ADMIN_ONLY,
        processing_status=FileProcessingStatus.FAILED,
    )
    _set_created(FileResource, w.f_pub.pk, w.now - timedelta(days=30))
    _set_created(FileResource, w.f_grp.pk, w.now - timedelta(days=5))
    _set_created(FileResource, w.f_adm.pk, w.now - timedelta(days=1))
    return w


def test_file_filters(w, files):
    c = _client(w.admin)

    def titles(query):
        return {r["title"] for r in _results(c.get(f"{FILES}?{query}&page_size=100"))}

    d10 = (w.now - timedelta(days=10)).date().isoformat()
    assert titles("visibility=group_only") == {"Group notes"}
    assert titles("visibility=admin_only") == {"Admin memo"}
    assert titles("status=ready") == {"Group notes"}
    assert titles("processing_status=failed") == {"Admin memo"}
    assert titles("status=pending") == {"F1", "F2"}
    assert titles(f"group={w.group.pk}") == {"Group notes"}
    assert titles(f"subject={w.prog.pk}") == {"F1", "F2"}
    assert titles(f"uploaded_by={w.uploader2.pk}") == {"Group notes", "Admin memo"}
    assert titles(f"created_after={d10}") == {"Group notes", "Admin memo", "F2"}
    assert titles(f"created_before={d10}") == {"F1"}
    assert titles(f"uploaded_by={w.uploader2.pk}&created_after={d10}&visibility=group_only") == {"Group notes"}
    assert titles("search=memo") == {"Admin memo"}


@pytest.mark.parametrize(
    "query",
    [
        "visibility=secret",
        "status=weird",
        "processing_status=weird",
        "uploaded_by=abc",
        "group=abc",
        "subject=abc",
        "created_after=2026/01/01",
        "created_before=bad",
    ],
)
def test_file_invalid_filters_400(w, files, query):
    assert _client(w.admin).get(f"{FILES}?{query}").status_code == 400


def test_file_permissions_and_query_count(w, files):
    assert APIClient().get(FILES).status_code == 401
    assert _client(w.print_staff).get(FILES).status_code == 403
    assert _client(w.student).get(FILES).status_code == 403
    c = _client(w.admin)
    small = _count_queries(c, f"{FILES}?page_size=100")
    for i in range(20):
        _file(w, f"Bulk{i}", subject=w.prog, group=w.group, visibility=FileVisibility.GROUP_ONLY)
    assert _count_queries(c, f"{FILES}?page_size=100") == small


# ---------------------------------------------------------------------------------------------------------------------
# lectures
# ---------------------------------------------------------------------------------------------------------------------

LECTURES = "/api/v1/dashboard/lectures/"


@pytest.fixture
def lectures(w, subjects):
    w.l_ready = w.lecture
    w.l_failed = _lecture(w, subject=w.arch, sha="c" * 64, title="Failed one", status=LectureProcessingStatus.FAILED)
    w.l_general = _lecture(
        w, subject=None, sha="d" * 64, title="General one", status=LectureProcessingStatus.QUEUED, faculty=w.fac
    )
    _set_created(Lecture, w.l_ready.pk, w.now - timedelta(days=30))
    _set_created(Lecture, w.l_failed.pk, w.now - timedelta(days=4))
    _set_created(Lecture, w.l_general.pk, w.now - timedelta(days=1))
    return w


def test_lecture_filters(w, lectures):
    c = _client(w.admin)

    def ids(query):
        return {r["id"] for r in _results(c.get(f"{LECTURES}?{query}&page_size=100"))}

    d10 = (w.now - timedelta(days=10)).date().isoformat()
    assert ids(f"subject={w.prog.pk}") == {w.l_ready.pk}
    assert ids("status=failed") == {w.l_failed.pk}
    assert ids("processing_status=failed") == {w.l_failed.pk}
    assert ids("processing_status=queued") == {w.l_general.pk}
    assert ids(f"created_after={d10}") == {w.l_failed.pk, w.l_general.pk}
    assert ids(f"created_before={d10}") == {w.l_ready.pk}
    assert ids(f"created_after={d10}&processing_status=failed") == {w.l_failed.pk}
    assert ids("scope=general") == {w.l_general.pk}  # existing filters keep working
    assert ids(f"faculty={w.fac.pk}") == {w.l_general.pk}


@pytest.mark.parametrize(
    "query",
    ["status=weird", "processing_status=weird", "subject=abc", "created_after=x", "created_before=2026-02-31"],
)
def test_lecture_invalid_filters_400(w, lectures, query):
    assert _client(w.admin).get(f"{LECTURES}?{query}").status_code == 400


def test_lecture_permissions_and_query_count(w, lectures):
    assert APIClient().get(LECTURES).status_code == 401
    assert _client(w.print_staff).get(LECTURES).status_code == 403
    assert _client(w.student).get(LECTURES).status_code == 403
    c = _client(w.admin)
    small = _count_queries(c, f"{LECTURES}?page_size=100")
    for i in range(15):
        _lecture(w, subject=w.prog, sha=f"{i:064x}", title=f"Bulk {i}")
    assert _count_queries(c, f"{LECTURES}?page_size=100") == small
