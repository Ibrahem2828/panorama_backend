"""Unified search: every type stays inside the queryset the per-resource endpoint already serves."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.announcements.models import Announcement, AnnouncementTargetUserType
from apps.files.models import FileResource, FileVisibility
from apps.groups.models import Group, GroupMembership, GroupMembershipStatus
from apps.lectures.models import Lecture, LectureProcessingStatus
from apps.universities.models import AcademicYear, Faculty, Major, Semester, Subject, University

KEYWORD = "Quantum"


def _user(email: str, role: str = UserRole.STUDENT, n: int = 0) -> User:
    return User.objects.create_user(
        full_name=email.split("@")[0],
        email=email,
        phone_number=f"+96398800{n:04d}",
        password="StrongPass123!",
        role=role,
    )


def _profile(user, university, faculty, major, year, semester, verified=True):
    StudentProfile.objects.create(
        user=user,
        university=university,
        faculty=faculty,
        major=major,
        academic_year=year,
        semester=semester,
        verification_status=StudentVerificationStatus.APPROVED if verified else StudentVerificationStatus.PENDING,
    )


def _lecture(subject, uploader, title, n, **extra) -> Lecture:
    values = {
        "status": LectureProcessingStatus.READY,
        "is_published": True,
        "page_count": 3,
        "viewer_pdf": ContentFile(b"%PDF-1.4", name=f"v{n}.pdf"),
    }
    values.update(extra)
    return Lecture.objects.create(
        subject=subject,
        title=title,
        original_file=ContentFile(b"%PDF-1.4", name=f"o{n}.pdf"),
        original_filename="o.pdf",
        original_mime_type="application/pdf",
        original_size=8,
        original_sha256=f"{n:064x}",
        uploaded_by=uploader,
        **values,
    )


def _file(uploader, title, n, **extra) -> FileResource:
    return FileResource.objects.create(
        title=title, file=ContentFile(b"%PDF-1.4", name=f"f{n}.pdf"), uploaded_by=uploader, **extra
    )


@pytest.fixture
def world(db):
    cache.clear()
    w = SimpleNamespace()
    w.uni = University.objects.create(name="Search University", code="SU")
    w.other_uni = University.objects.create(name="Foreign University", code="FU")
    w.fac = Faculty.objects.create(university=w.uni, name="Science", code="SC")
    w.other_fac = Faculty.objects.create(university=w.other_uni, name="Foreign science", code="FS")
    w.major = Major.objects.create(faculty=w.fac, name="Physics", code="PHY")
    w.other_major = Major.objects.create(faculty=w.fac, name="Chemistry", code="CHE")
    w.foreign_major = Major.objects.create(faculty=w.other_fac, name="Foreign physics", code="FPH")
    w.year = AcademicYear.objects.create(name="Year 1", order=91)
    w.sem = Semester.objects.create(name="Semester 1", order=91)
    w.admin = _user("search-admin@example.test", UserRole.ADMIN, 1)

    w.subject = Subject.objects.create(
        faculty=w.fac, major=w.major, academic_year=w.year, semester=w.sem, name=f"{KEYWORD} mechanics", code="QM1"
    )
    w.subject_hidden = {
        "inactive": Subject.objects.create(
            faculty=w.fac,
            major=w.major,
            academic_year=w.year,
            semester=w.sem,
            name=f"{KEYWORD} inactive",
            is_active=False,
        ),
        "deleted": Subject.objects.create(
            faculty=w.fac,
            major=w.major,
            academic_year=w.year,
            semester=w.sem,
            name=f"{KEYWORD} deleted",
            is_deleted=True,
        ),
        "other_major": Subject.objects.create(
            faculty=w.fac, major=w.other_major, academic_year=w.year, semester=w.sem, name=f"{KEYWORD} chemistry"
        ),
        "foreign": Subject.objects.create(
            faculty=w.other_fac, major=w.foreign_major, academic_year=w.year, semester=w.sem, name=f"{KEYWORD} foreign"
        ),
    }

    w.student = _user("search-student@example.test", n=2)
    _profile(w.student, w.uni, w.fac, w.major, w.year, w.sem)
    w.unverified = _user("search-unverified@example.test", n=3)
    _profile(w.unverified, w.uni, w.fac, w.major, w.year, w.sem, verified=False)
    w.foreign_student = _user("search-foreign@example.test", n=4)
    _profile(w.foreign_student, w.other_uni, w.other_fac, w.foreign_major, w.year, w.sem)
    w.normal = _user("search-normal@example.test", UserRole.NORMAL_USER, 5)

    # lectures
    w.lecture = _lecture(w.subject, w.admin, f"{KEYWORD} lecture one", 1)
    w.lecture_hidden = {
        "unpublished": _lecture(w.subject, w.admin, f"{KEYWORD} draft", 2, is_published=False),
        "processing": _lecture(
            w.subject, w.admin, f"{KEYWORD} converting", 3, status=LectureProcessingStatus.RENDERING
        ),
        "deleted": _lecture(w.subject, w.admin, f"{KEYWORD} gone", 4, is_deleted=True),
        "other_major": _lecture(w.subject_hidden["other_major"], w.admin, f"{KEYWORD} chem lecture", 5),
        "foreign": _lecture(w.subject_hidden["foreign"], w.admin, f"{KEYWORD} foreign lecture", 6),
    }

    # groups
    w.group = Group.objects.create(name=f"{KEYWORD} study group", university=w.uni, created_by=w.admin)
    w.group_hidden = {
        "inactive": Group.objects.create(
            name=f"{KEYWORD} inactive group", university=w.uni, created_by=w.admin, is_active=False
        ),
        "deleted": Group.objects.create(
            name=f"{KEYWORD} deleted group", university=w.uni, created_by=w.admin, is_deleted=True
        ),
        "foreign": Group.objects.create(name=f"{KEYWORD} foreign group", university=w.other_uni, created_by=w.admin),
        "other_major": Group.objects.create(
            name=f"{KEYWORD} chem group", university=w.uni, major=w.other_major, created_by=w.admin
        ),
    }

    # files
    w.file = _file(w.admin, f"{KEYWORD} notes", 1, subject=w.subject)
    w.file_hidden = {
        "admin_only": _file(w.admin, f"{KEYWORD} secret", 2, visibility=FileVisibility.ADMIN_ONLY),
        "inactive": _file(w.admin, f"{KEYWORD} inactive file", 3, is_active=False),
        "deleted": _file(w.admin, f"{KEYWORD} deleted file", 4, is_deleted=True),
        "verified_only": _file(w.admin, f"{KEYWORD} verified", 5, visibility=FileVisibility.VERIFIED_STUDENTS_ONLY),
        "other_major": _file(
            w.admin,
            f"{KEYWORD} chem file",
            6,
            visibility=FileVisibility.MAJOR_ONLY,
            major=w.other_major,
            academic_year=w.year,
        ),
    }

    # announcements
    w.announcement = Announcement.objects.create(
        title=f"{KEYWORD} news", description="General news", created_by=w.admin
    )
    now = timezone.now()
    w.announcement_hidden = {
        "inactive": Announcement.objects.create(
            title=f"{KEYWORD} inactive", description="x", created_by=w.admin, is_active=False
        ),
        "expired": Announcement.objects.create(
            title=f"{KEYWORD} expired", description="x", created_by=w.admin, ends_at=now - timedelta(days=1)
        ),
        "future": Announcement.objects.create(
            title=f"{KEYWORD} future", description="x", created_by=w.admin, starts_at=now + timedelta(days=1)
        ),
        "deleted": Announcement.objects.create(
            title=f"{KEYWORD} deleted", description="x", created_by=w.admin, is_deleted=True
        ),
        "other_major": Announcement.objects.create(
            title=f"{KEYWORD} other major", description="x", created_by=w.admin, target_major=w.other_major
        ),
        "admins": Announcement.objects.create(
            title=f"{KEYWORD} admins",
            description="x",
            created_by=w.admin,
            target_user_type=AnnouncementTargetUserType.ADMINS,
        ),
    }
    w.announcement_verified = Announcement.objects.create(
        title=f"{KEYWORD} verified news",
        description="x",
        created_by=w.admin,
        target_user_type=AnnouncementTargetUserType.VERIFIED_STUDENTS,
    )
    return w


def _search(user, query=KEYWORD, **params):
    client = APIClient()
    if user is not None:
        client.force_authenticate(user)
    return client.get("/api/v1/search/", {"q": query, **params})


def _ids(response, kind):
    return {row["id"] for row in response.data["data"][kind]}


@pytest.mark.django_db
def test_verified_student_sees_exactly_what_the_endpoints_allow(world):
    w = world
    response = _search(w.student)
    assert response.status_code == status.HTTP_200_OK
    data = response.data["data"]
    assert _ids(response, "subjects") == {w.subject.id}
    assert _ids(response, "lectures") == {w.lecture.id}
    assert _ids(response, "groups") == {w.group.id}
    assert _ids(response, "files") == {w.file.id, w.file_hidden["verified_only"].id}  # verified-only files are theirs
    assert _ids(response, "announcements") == {w.announcement.id, w.announcement_verified.id}
    assert data["counts"] == {"subjects": 1, "lectures": 1, "groups": 1, "files": 2, "announcements": 2}


@pytest.mark.django_db
def test_results_are_a_subset_of_the_per_resource_endpoints(world):
    w = world
    client = APIClient()
    client.force_authenticate(w.student)
    search = _search(w.student).data["data"]

    def listed(url, **params):
        return {row["id"] for row in client.get(url, {"page_size": 100, **params}).data["data"]["results"]}

    assert {r["id"] for r in search["lectures"]} <= listed("/api/v1/lectures/")
    assert {r["id"] for r in search["groups"]} <= listed("/api/v1/groups/")
    assert {r["id"] for r in search["files"]} <= listed("/api/v1/files/")
    assert {r["id"] for r in search["announcements"]} <= listed("/api/v1/announcements/")
    assert {r["id"] for r in search["subjects"]} <= listed(f"/api/v1/majors/{w.major.id}/subjects/")


@pytest.mark.django_db
def test_hidden_rows_never_leak_to_the_verified_student(world):
    w = world
    response = _search(w.student)
    hidden = (
        {s.id for s in w.subject_hidden.values()},
        {r.id for r in w.lecture_hidden.values()},
        {r.id for r in w.group_hidden.values()},
        {r.id for r in w.file_hidden.values() if r is not w.file_hidden["verified_only"]},
        {r.id for r in w.announcement_hidden.values()},
    )
    for kind, ids in zip(("subjects", "lectures", "groups", "files", "announcements"), hidden, strict=True):
        assert not (_ids(response, kind) & ids), kind
    # The verified-only file is legitimately visible to a verified student (same as /files/).
    assert w.file_hidden["verified_only"].id in _ids(response, "files")


@pytest.mark.django_db
def test_cross_university_isolation(world):
    w = world
    response = _search(w.foreign_student)
    assert response.status_code == 200
    assert _ids(response, "groups") == {w.group_hidden["foreign"].id}
    assert _ids(response, "subjects") == {w.subject_hidden["foreign"].id}
    # The foreign student's profile has no approved lecture for their subject path set up: only their own major.
    assert w.lecture.id not in _ids(response, "lectures")
    assert w.group.id not in _ids(response, "groups")
    assert w.subject.id not in _ids(response, "subjects")


@pytest.mark.django_db
def test_unverified_student_and_normal_user_only_get_what_they_can_already_see(world):
    w = world
    for user in (w.unverified, w.normal):
        response = _search(user)
        assert response.status_code == 200
        assert _ids(response, "lectures") == set()
        assert _ids(response, "groups") == set()
        assert w.file_hidden["verified_only"].id not in _ids(response, "files")
        assert w.announcement_verified.id not in _ids(response, "announcements")
    assert _ids(_search(w.unverified), "files") == {w.file.id}
    assert _ids(_search(w.normal), "subjects") == set()
    assert _ids(_search(w.normal), "files") == {w.file.id}
    # Normal users get exactly what /announcements/ serves them (that endpoint ignores academic targeting for them).
    client = APIClient()
    client.force_authenticate(w.normal)
    listed = {row["id"] for row in client.get("/api/v1/announcements/?page_size=100").data["data"]["results"]}
    assert _ids(_search(w.normal), "announcements") == listed
    assert w.announcement.id in listed


@pytest.mark.django_db
def test_membership_makes_group_only_content_visible_not_search_itself(world):
    w = world
    private = Group.objects.create(name=f"{KEYWORD} private", university=w.uni, major=w.other_major, created_by=w.admin)
    private_file = _file(w.admin, f"{KEYWORD} members file", 20, visibility=FileVisibility.GROUP_ONLY, group=private)
    assert private_file.id not in _ids(_search(w.student), "files")
    GroupMembership.objects.create(group=private, user=w.student, status=GroupMembershipStatus.APPROVED)
    after = _search(w.student)
    assert private_file.id in _ids(after, "files")
    assert private.id in _ids(after, "groups")


@pytest.mark.django_db
def test_query_validation(world):
    w = world
    assert _search(w.student, "a").status_code == status.HTTP_400_BAD_REQUEST
    assert _search(w.student, " a ").status_code == status.HTTP_400_BAD_REQUEST
    assert _search(w.student, "x" * 51).status_code == status.HTTP_400_BAD_REQUEST
    assert _search(w.student, "ab").status_code == status.HTTP_200_OK
    assert _search(w.student, "x" * 50).status_code == status.HTTP_200_OK
    client = APIClient()
    client.force_authenticate(w.student)
    assert client.get("/api/v1/search/").status_code == status.HTTP_400_BAD_REQUEST
    assert _search(w.student, types="subjects,bogus").status_code == status.HTTP_400_BAD_REQUEST
    assert _search(w.student, limit="abc").status_code == status.HTTP_400_BAD_REQUEST
    assert _search(w.student, limit="0").status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_matching_is_case_insensitive_and_wildcards_are_literal(world):
    w = world
    assert _ids(_search(w.student, KEYWORD.lower()), "lectures") == {w.lecture.id}
    assert _ids(_search(w.student, KEYWORD.upper()), "lectures") == {w.lecture.id}
    wildcard = _search(w.student, "%%")
    assert wildcard.status_code == 200
    assert wildcard.data["data"]["counts"] == dict.fromkeys(
        ("subjects", "lectures", "groups", "files", "announcements"), 0
    )
    # description is searched too
    assert _ids(_search(w.student, "general news"), "announcements") == {w.announcement.id}


@pytest.mark.django_db
def test_types_filter_limit_and_counts(world):
    w = world
    for n in range(30, 38):
        _lecture(w.subject, w.admin, f"{KEYWORD} extra {n}", n)
    only = _search(w.student, types="lectures,files")
    data = only.data["data"]
    assert data["groups"] == [] and data["subjects"] == [] and data["announcements"] == []
    assert data["counts"]["groups"] == 0  # not searched at all
    assert data["counts"]["lectures"] == 9 and len(data["lectures"]) == 5  # default limit, total in counts
    assert len(_search(w.student, limit=3).data["data"]["lectures"]) == 3
    capped = _search(w.student, limit=500)
    assert capped.data["data"]["limit"] == 20 and len(capped.data["data"]["lectures"]) == 9


@pytest.mark.django_db
def test_result_shape_carries_parent_context_and_no_internal_fields(world):
    w = world
    data = _search(w.student).data["data"]
    lecture = data["lectures"][0]
    assert lecture["type"] == "lecture"
    assert lecture["subject_id"] == w.subject.id and lecture["subject_name"] == w.subject.name
    assert "original_file" not in lecture and "viewer_pdf" not in lecture and "original_sha256" not in lecture
    file_row = next(row for row in data["files"] if row["id"] == w.file.id)
    assert file_row["subject_name"] == w.subject.name
    assert "file" not in file_row and "sha256" not in file_row
    assert data["subjects"][0]["code"] == "QM1"
    assert data["groups"][0]["type"] == "group"


@pytest.mark.django_db
def test_staff_see_what_the_manager_endpoints_serve_and_nothing_more(world):
    w = world
    response = _search(w.admin)
    assert response.status_code == 200
    # admins hold lectures.manage / see every file, but still no hidden-by-deletion rows
    assert w.lecture_hidden["deleted"].id not in _ids(response, "lectures")
    assert w.file_hidden["deleted"].id not in _ids(response, "files")
    assert w.file_hidden["inactive"].id not in _ids(response, "files")
    assert _ids(response, "groups") == set()  # groups search is for students


@pytest.mark.django_db
def test_requires_authentication(world):
    assert _search(None).status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
def test_search_is_throttled_per_user(world, monkeypatch):
    from apps.common.throttles import SearchRateThrottle

    monkeypatch.setitem(SearchRateThrottle.THROTTLE_RATES, "search", "3/min")  # restored after the test
    cache.clear()
    w = world
    codes = [_search(w.student).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3:] == [429, 429]
    assert _search(w.foreign_student).status_code == 200  # the budget is per user
