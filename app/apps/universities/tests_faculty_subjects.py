"""Subjects belong to a faculty; the major is an optional specialization tag."""

from __future__ import annotations

import copy
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from pypdf import PdfWriter
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.common.reference_import import ReferenceDataError, import_reference_data
from apps.groups.models import Group, GroupMembership, GroupMembershipStatus
from apps.groups.services import is_student_eligible_for_group
from apps.lectures.models import Lecture, LectureProcessingStatus
from apps.lectures.services import accessible_lectures_for_user, user_can_view_lecture
from apps.universities.models import AcademicYear, Faculty, Major, Semester, Subject, University
from apps.verification.models import VerificationRequest

pytestmark = pytest.mark.django_db

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)
EXAMPLE = Path(__file__).resolve().parents[3] / "docs" / "reference-data.example.json"


def _user(email, role=UserRole.STUDENT, n=1):
    return User.objects.create_user(
        full_name=email.split("@")[0],
        email=email,
        phone_number=f"+96397700{n:04d}",
        password="StrongPass123!",
        role=role,
        is_phone_verified=True,
    )


@pytest.fixture
def w(db):
    w = SimpleNamespace()
    w.uni = University.objects.create(name="Private University", code="PU")
    w.fac = Faculty.objects.create(university=w.uni, name="Informatics", code="4")
    w.other_fac = Faculty.objects.create(university=w.uni, name="Pharmacy", code="3")
    w.se = Major.objects.create(faculty=w.fac, name="Software", code="SE")
    w.ai = Major.objects.create(faculty=w.fac, name="AI", code="AI")
    w.foreign_major = Major.objects.create(faculty=w.other_fac, name="Clinical", code="CL")
    w.y1 = AcademicYear.objects.create(name="Year 1", order=1)
    w.y3 = AcademicYear.objects.create(name="Year 3", order=3)
    w.s1 = Semester.objects.create(name="Semester 1", order=1)
    w.s2 = Semester.objects.create(name="Semester 2", order=2)
    w.general = Subject.objects.create(faculty=w.fac, academic_year=w.y1, semester=w.s1, name="Programming", code="P1")
    w.se_subject = Subject.objects.create(
        faculty=w.fac, major=w.se, academic_year=w.y3, semester=w.s2, name="Architecture", code="A3"
    )
    w.ai_subject = Subject.objects.create(faculty=w.fac, major=w.ai, academic_year=w.y3, semester=w.s1, name="ML")
    w.foreign_subject = Subject.objects.create(faculty=w.other_fac, academic_year=w.y1, semester=w.s1, name="Chem")
    w.admin = _user("admin@subj.test", UserRole.ADMIN, 1)
    return w


def _student(w, email, n, *, major=None, year=None, semester=None, faculty="fac", status_=None):
    user = _user(email, n=n)
    StudentProfile.objects.create(
        user=user,
        university=w.uni,
        faculty=getattr(w, faculty) if faculty else None,
        major=major,
        academic_year=year or w.y1,
        semester=semester or w.s1,
        student_number=f"4{n:06d}",
        verification_status=status_ or StudentVerificationStatus.APPROVED,
    )
    return user


def _results(response):
    return response.data["data"]["results"]


# --- model ----------------------------------------------------------------------------------------------------


def test_subject_constraints_with_and_without_major(w):
    with pytest.raises(IntegrityError), transaction.atomic():
        Subject.objects.create(faculty=w.fac, academic_year=w.y1, semester=w.s1, name="Programming")
    with pytest.raises(IntegrityError), transaction.atomic():
        Subject.objects.create(faculty=w.fac, major=w.se, academic_year=w.y3, semester=w.s2, name="Architecture")
    # the same name is fine in another semester, another major, or tagged vs untagged
    Subject.objects.create(faculty=w.fac, academic_year=w.y1, semester=w.s2, name="Programming")
    Subject.objects.create(faculty=w.fac, major=w.ai, academic_year=w.y3, semester=w.s2, name="Architecture")
    Subject.objects.create(faculty=w.fac, academic_year=w.y3, semester=w.s2, name="Architecture")


def test_subject_code_is_unique_per_faculty_across_majors(w):
    with pytest.raises(IntegrityError), transaction.atomic():
        Subject.objects.create(faculty=w.fac, major=w.ai, academic_year=w.y1, semester=w.s1, name="X", code="p1")
    # another faculty may reuse the code; blank codes never clash
    Subject.objects.create(faculty=w.other_fac, academic_year=w.y1, semester=w.s1, name="X", code="P1")
    Subject.objects.create(faculty=w.fac, academic_year=w.y1, semester=w.s1, name="B1")
    Subject.objects.create(faculty=w.fac, academic_year=w.y1, semester=w.s1, name="B2")


def test_subject_clean_rejects_major_of_another_faculty(w):
    subject = Subject(faculty=w.fac, major=w.foreign_major, academic_year=w.y1, semester=w.s1, name="Bad")
    with pytest.raises(ValidationError) as exc:
        subject.full_clean()
    assert "major" in exc.value.message_dict


# --- groups ---------------------------------------------------------------------------------------------------


def test_group_may_pin_only_university_faculty_and_subject(w):
    Group(name="Prog", university=w.uni, faculty=w.fac, subject=w.general, created_by=w.admin).full_clean()
    # an untagged subject fits a group that pins a major, a tagged one only its own major
    Group(name="g2", university=w.uni, faculty=w.fac, major=w.se, subject=w.general, created_by=w.admin).full_clean()
    with pytest.raises(ValidationError) as exc:
        Group(
            name="g3", university=w.uni, faculty=w.fac, major=w.ai, subject=w.se_subject, created_by=w.admin
        ).full_clean()
    assert "subject" in exc.value.message_dict
    with pytest.raises(ValidationError):
        Group(name="g4", university=w.uni, faculty=w.fac, subject=w.foreign_subject, created_by=w.admin).full_clean()
    with pytest.raises(ValidationError):
        Group(
            name="g5", university=w.uni, faculty=w.fac, academic_year=w.y3, subject=w.general, created_by=w.admin
        ).full_clean()


def test_subject_group_is_open_to_every_student_of_the_faculty(w):
    group = Group.objects.create(name="Prog", university=w.uni, faculty=w.fac, subject=w.general, created_by=w.admin)
    year3_no_major = _student(w, "y2@subj.test", 2, year=w.y3)
    senior_with_major = _student(w, "y3@subj.test", 3, major=w.se, year=w.y3, semester=w.s2)
    other_faculty = _student(w, "of@subj.test", 4, faculty="other_fac")
    assert is_student_eligible_for_group(year3_no_major, group)
    assert is_student_eligible_for_group(senior_with_major, group)
    assert not is_student_eligible_for_group(other_faculty, group)


# --- API ------------------------------------------------------------------------------------------------------


def test_public_faculty_subjects_endpoint(w):
    client = APIClient()
    base = f"/api/v1/faculties/{w.fac.id}/subjects/"
    response = client.get(base)
    assert response.status_code == status.HTTP_200_OK
    assert {s["name"] for s in _results(response)} == {"Programming", "Architecture", "ML"}
    assert {s["name"] for s in _results(client.get(f"{base}?major={w.se.id}"))} == {"Programming", "Architecture"}
    assert {s["name"] for s in _results(client.get(f"{base}?major={w.ai.id}"))} == {"Programming", "ML"}
    by_year = _results(client.get(f"{base}?academic_year={w.y3.id}&semester={w.s1.id}"))
    assert [s["name"] for s in by_year] == ["ML"]
    assert [s["name"] for s in _results(client.get(f"{base}?search=progr"))] == ["Programming"]
    assert client.get(f"{base}?major=abc").status_code == 400


def test_public_endpoints_hide_inactive_and_deleted(w):
    client = APIClient()
    base = f"/api/v1/faculties/{w.fac.id}/subjects/"

    def names():
        return {s["name"] for s in _results(client.get(base))}

    w.general.is_active = False
    w.general.save()
    w.ai_subject.is_deleted = True
    w.ai_subject.save()
    w.se.is_active = False
    w.se.save()
    assert names() == set()
    w.general.is_active = True
    w.general.save()
    assert names() == {"Programming"}
    assert _results(client.get(f"/api/v1/majors/{w.se.id}/subjects/")) == []  # inactive major
    w.se.is_active = True
    w.se.save()
    w.y3.is_active = False
    w.y3.save()
    assert names() == {"Programming"}


def test_major_subjects_endpoint_is_a_superset_with_faculty_level_subjects(w):
    client = APIClient()
    response = client.get(f"/api/v1/majors/{w.se.id}/subjects/")
    assert response.status_code == status.HTTP_200_OK
    rows = {s["name"]: s for s in _results(response)}
    assert set(rows) == {"Programming", "Architecture"}
    general = rows["Programming"]
    assert general["major"] is None and general["major_detail"] is None and general["major_name"] is None
    assert general["faculty"] == w.fac.id
    assert general["faculty_detail"]["id"] == w.fac.id
    assert general["faculty_detail"]["name"] == "Informatics"
    assert general["faculty_detail"]["code"] == "4"
    assert general["faculty_detail"]["university"]["code"] == "PU"
    assert rows["Architecture"]["major_detail"]["id"] == w.se.id
    assert rows["Architecture"]["academic_year_name"] == "Year 3"
    assert _results(client.get("/api/v1/majors/999999/subjects/")) == []


def test_dashboard_subject_create_update_and_validation(w):
    client = APIClient()
    client.force_authenticate(w.admin)
    base = {"academic_year": w.y1.id, "semester": w.s1.id}
    url = "/api/v1/dashboard/subjects/"

    created = client.post(url, {**base, "faculty": w.fac.id, "name": "Math"}, format="json")
    assert created.status_code == status.HTTP_201_CREATED, created.data
    assert created.data["data"]["major"] is None

    derived = client.post(url, {**base, "major": w.se.id, "name": "Graphs"}, format="json")
    assert derived.status_code == status.HTTP_201_CREATED, derived.data
    assert derived.data["data"]["faculty"] == w.fac.id

    missing = client.post(url, {**base, "name": "Nope"}, format="json")
    assert missing.status_code == 400
    assert "faculty" in json.dumps(missing.data)

    mismatch = client.post(
        url, {**base, "faculty": w.fac.id, "major": w.foreign_major.id, "name": "Mix"}, format="json"
    )
    assert mismatch.status_code == 400
    assert "major" in json.dumps(mismatch.data)

    assert client.post(url, {**base, "faculty": w.fac.id, "name": "Math"}, format="json").status_code == 400
    dup_code = client.post(url, {**base, "faculty": w.fac.id, "name": "Other", "code": "p1"}, format="json")
    assert dup_code.status_code == 400
    assert "code" in json.dumps(dup_code.data)

    detail = f"{url}{created.data['data']['id']}/"
    tag = client.patch(detail, {"major": w.ai.id}, format="json")
    assert tag.status_code == 200 and tag.data["data"]["major"] == w.ai.id
    untag = client.patch(detail, {"major": None}, format="json")
    assert untag.status_code == 200 and untag.data["data"]["major"] is None
    assert client.patch(detail, {"major": w.foreign_major.id}, format="json").status_code == 400
    moved = client.patch(detail, {"faculty": w.other_fac.id}, format="json")
    assert moved.status_code == 200 and moved.data["data"]["faculty"] == w.other_fac.id


def test_dashboard_subject_filters(w):
    client = APIClient()
    client.force_authenticate(w.admin)

    def names(query):
        return {s["name"] for s in _results(client.get(f"/api/v1/dashboard/subjects/?{query}"))}

    assert names(f"faculty={w.other_fac.id}") == {"Chem"}
    assert names(f"major={w.se.id}") == {"Architecture"}
    assert names("has_major=true") == {"Architecture", "ML"}
    assert names("has_major=false") == {"Programming", "Chem"}
    assert names(f"faculty={w.fac.id}&academic_year={w.y3.id}&semester={w.s1.id}") == {"ML"}
    assert names("search=P1") == {"Programming"}
    w.general.is_active = False
    w.general.save()
    assert names("is_active=false") == {"Programming"}


# --- access ---------------------------------------------------------------------------------------------------


def _lecture(subject, uploader, n):
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(output)
    return Lecture.objects.create(
        subject=subject,
        title=f"Lecture {n}",
        original_file=ContentFile(output.getvalue(), name=f"o{n}.pdf"),
        original_filename="o.pdf",
        original_mime_type="application/pdf",
        original_size=10,
        original_sha256=f"{n:064x}",
        viewer_pdf=ContentFile(output.getvalue(), name=f"v{n}.pdf"),
        status=LectureProcessingStatus.READY,
        is_published=True,
        page_count=1,
        uploaded_by=uploader,
    )


def test_lecture_access_is_faculty_based_without_year_or_semester(w):
    general = _lecture(w.general, w.admin, 1)  # year 1 / semester 1, no major
    se_lecture = _lecture(w.se_subject, w.admin, 2)  # year 3 / semester 2, major SE
    ai_lecture = _lecture(w.ai_subject, w.admin, 3)
    foreign = _lecture(w.foreign_subject, w.admin, 4)
    draft = _lecture(w.general, w.admin, 5)
    draft.is_published = False
    draft.save()

    year3_no_major = _student(w, "a@subj.test", 2, year=w.y3, semester=w.s2)
    se_student = _student(w, "b@subj.test", 3, major=w.se, year=w.y1, semester=w.s1)
    outsider = _student(w, "c@subj.test", 4, faculty="other_fac")
    pending = _student(w, "d@subj.test", 5, status_=StudentVerificationStatus.PENDING)
    no_faculty = _student(w, "e@subj.test", 6, faculty=None)
    no_group = _student(w, "f@subj.test", 7)

    # Lectures also require an approved membership in the subject's study group; everyone below belongs to all of
    # them (except `no_group`), so what is asserted is the faculty/major rule on top of membership.
    for subject in (w.general, w.se_subject, w.ai_subject, w.foreign_subject):
        group = Group.objects.filter(subject=subject).first() or Group.objects.create(
            name=f"Group {subject.id}", university=w.uni, faculty=subject.faculty, subject=subject, created_by=w.admin
        )
        for member in (year3_no_major, se_student, outsider, pending, no_faculty):
            GroupMembership.objects.get_or_create(
                group=group, user=member, defaults={"status": GroupMembershipStatus.APPROVED}
            )

    assert not user_can_view_lecture(no_group, general)  # right faculty, but not in the subject's group
    assert user_can_view_lecture(year3_no_major, general)  # retaking a year-1 subject
    assert not user_can_view_lecture(year3_no_major, se_lecture)  # tagged with a major the student lacks
    assert user_can_view_lecture(se_student, general) and user_can_view_lecture(se_student, se_lecture)
    assert not user_can_view_lecture(se_student, ai_lecture)
    assert user_can_view_lecture(outsider, foreign) and not user_can_view_lecture(outsider, general)
    assert not user_can_view_lecture(pending, general) and not user_can_view_lecture(no_faculty, general)
    assert not user_can_view_lecture(year3_no_major, draft)  # unpublished
    assert user_can_view_lecture(w.admin, draft)  # staff rules unchanged
    assert set(accessible_lectures_for_user(se_student)) == {general, se_lecture}
    assert set(accessible_lectures_for_user(year3_no_major)) == {general}
    assert set(accessible_lectures_for_user(outsider)) == {foreign}
    assert set(accessible_lectures_for_user(no_faculty)) == set()


def test_search_subjects_uses_faculty_rule(w):
    client = APIClient()
    no_major = _student(w, "s1@subj.test", 2)
    with_major = _student(w, "s2@subj.test", 3, major=w.se)
    no_faculty = _student(w, "s3@subj.test", 4, faculty=None)

    def found(user, q):
        client.force_authenticate(user)
        data = client.get(f"/api/v1/search/?q={q}&types=subjects").data["data"]
        return {s["name"] for s in data["subjects"]}

    assert found(no_major, "Pro") == {"Programming"}
    assert found(no_major, "Arch") == set()
    assert found(with_major, "Arch") == {"Architecture"}
    assert found(with_major, "ML") == set()
    assert found(no_faculty, "Pro") == set()


# --- verification ---------------------------------------------------------------------------------------------


def _submit_payload(w, **extra):
    return {
        "university": w.uni.id,
        "faculty": w.fac.id,
        "academic_year": w.y1.id,
        "semester": w.s1.id,
        "student_number": "4150094",
        "card_image": SimpleUploadedFile("card.png", PNG, content_type="image/png"),
        **extra,
    }


def test_verification_without_major_and_approval_copies_null_major(w):
    user = _user("v@subj.test", n=9)
    StudentProfile.objects.create(user=user)
    client = APIClient()
    client.force_authenticate(user)
    response = client.post("/api/v1/verification/submit/", _submit_payload(w), format="multipart")
    assert response.status_code == status.HTTP_201_CREATED, response.data
    assert response.data["data"]["major"] is None and response.data["data"]["major_detail"] is None
    request = VerificationRequest.objects.get(user=user)
    assert request.major is None

    client.force_authenticate(w.admin)
    listing = client.get("/api/v1/dashboard/verifications/")
    assert listing.status_code == 200 and _results(listing)[0]["major"] is None
    approved = client.post(f"/api/v1/dashboard/verifications/{request.id}/approve/", {}, format="json")
    assert approved.status_code == 200, approved.data
    user.student_profile.refresh_from_db()
    assert user.student_profile.major is None
    assert user.student_profile.verification_status == StudentVerificationStatus.APPROVED


def test_verification_major_must_match_faculty_when_given(w):
    user = _user("v2@subj.test", n=10)
    StudentProfile.objects.create(user=user)
    client = APIClient()
    client.force_authenticate(user)
    bad = client.post("/api/v1/verification/submit/", _submit_payload(w, major=w.foreign_major.id), format="multipart")
    assert bad.status_code == 400
    ok = client.post("/api/v1/verification/submit/", _submit_payload(w, major=w.se.id), format="multipart")
    assert ok.status_code == 201 and ok.data["data"]["major"] == w.se.id


def test_student_profile_can_be_saved_without_major(w):
    user = _user("p@subj.test", n=11)
    StudentProfile.objects.create(user=user)
    client = APIClient()
    client.force_authenticate(user)
    response = client.patch(
        "/api/v1/students/me/profile/",
        {
            "university": w.uni.id,
            "faculty": w.fac.id,
            "academic_year": w.y1.id,
            "semester": w.s1.id,
            "student_number": "4150095",
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    assert response.data["data"]["major"] is None


# --- reference import -----------------------------------------------------------------------------------------


def _document():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _faculty(doc):
    return doc["universities"][0]["faculties"][0]


def test_import_faculty_level_subjects_idempotent_and_tagged():
    doc = _document()
    first = import_reference_data(doc)
    assert first.counts["subjects"]["created"] == 4
    general = Subject.objects.get(code="EX-G01")
    tagged = Subject.objects.get(code="EX-G02")
    assert general.major is None and general.faculty.code == "1"
    assert (
        Subject.objects.filter(pk=tagged.pk, major__code="EX-MAJ-1").exists()
        and tagged.faculty_id == general.faculty_id
    )
    assert Subject.objects.get(code="EX-101").faculty_id == general.faculty_id  # nested form fills the faculty

    assert import_reference_data(doc).counts["subjects"]["unchanged"] == 4

    _faculty(doc)["subjects"][0]["description"] = "changed"
    assert import_reference_data(doc).counts["subjects"]["updated"] == 1
    assert Subject.objects.filter(major__isnull=True).count() == 1


def test_import_major_reference_by_name_and_dry_run():
    doc = _document()
    _faculty(doc)["subjects"][1]["major"] = "EXAMPLE MAJOR ONE"
    assert import_reference_data(doc, dry_run=True).counts["subjects"]["created"] == 4
    assert Subject.objects.count() == 0
    import_reference_data(doc)
    assert Subject.objects.filter(code="EX-G02", major__name="EXAMPLE MAJOR ONE").exists()


@pytest.mark.parametrize(
    ("mutate", "path"),
    [
        (lambda f: f["subjects"][1].update(major="NOPE"), "faculties[0].subjects[1].major"),
        (lambda f: f["subjects"][1].update(major=3), "faculties[0].subjects[1].major"),
        (lambda f: f["subjects"][0].update(semester="NOPE"), "faculties[0].subjects[0].semester"),
        (lambda f: f["subjects"][0].update(code="EX-101"), "majors[0].subjects[0]: duplicate subject code"),
        (lambda f: f["subjects"].append(copy.deepcopy(f["subjects"][0])), "faculties[0].subjects[2]"),
        (lambda f: f["subjects"][0].update(bogus=1), "faculties[0].subjects[0].bogus"),
        (lambda f: f.update(subjects={}), "faculties[0].subjects: must be a list"),
        (
            lambda f: f["subjects"][1].update(major="EXAMPLE MAJOR TWO (no code)"),
            "faculties[0].subjects[1].major",
        ),  # a major of another faculty
    ],
)
def test_import_faculty_subject_errors_name_the_path(mutate, path):
    doc = _document()
    mutate(_faculty(doc))
    with pytest.raises(ReferenceDataError) as exc:
        import_reference_data(doc)
    assert path in "\n".join(exc.value.errors)
    assert Subject.objects.count() == 0
