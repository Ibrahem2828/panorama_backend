"""One device change per ACADEMIC TERM, and offline licenses die with a device switch/reset."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.choices import AccountDeviceBoundVia, StudentVerificationStatus, UserRole
from apps.accounts.devices import counting_window, switch_state
from apps.accounts.models import AccountDevice, StudentProfile, User
from apps.accounts.tests.test_device_binding import (
    DEVICE_A,
    DEVICE_B,
    DEVICE_C,
    _login,
    _mismatch_token,
    _switch,
    admin,  # noqa: F401
    enforced,  # noqa: F401
    student,  # noqa: F401
)
from apps.audit.models import AuditAction, AuditLog
from apps.files.models import FileOfflineLicense
from apps.files.tests_viewer import _ready_file
from apps.lectures.models import LectureOfflineLicense
from apps.universities.models import CurrentTerm, Semester, University

pytestmark = pytest.mark.django_db


@pytest.fixture
def term(db):
    first = Semester.objects.create(name="Fall", order=51)
    second = Semester.objects.create(name="Spring", order=52)
    row = CurrentTerm.objects.create(
        pk=CurrentTerm.SINGLETON_PK, semester=first, label="Fall", started_at=timezone.now() - timedelta(days=30)
    )
    return {"row": row, "first": first, "second": second}


def _roll_term(term, started_at):
    row = term["row"]
    row.semester = term["second"]
    row.started_at = started_at
    row.save()


def test_without_a_current_term_the_rolling_window_applies(enforced, student):  # noqa: F811
    start, scope = counting_window()
    assert scope == "rolling" and start < timezone.now() - timedelta(days=100)
    assert switch_state(student)["limit_scope"] == "rolling"


def test_switch_allowance_is_per_term_and_resets_at_rollover(enforced, student, term):  # noqa: F811
    _login(student, DEVICE_A)
    assert _switch(_mismatch_token(student, DEVICE_B), DEVICE_B).status_code == 200
    state = switch_state(student)
    assert state["limit_scope"] == "term" and state["switches_remaining"] == 0
    assert state["term_started_at"] is not None
    refused = _switch(_login(student, DEVICE_C).data["errors"]["device_switch_token"], DEVICE_C)
    assert refused.status_code == 403 and refused.data["code"] == "DEVICE_SWITCH_LIMIT_REACHED"
    assert refused.data["errors"]["contact_admin"] is True
    assert refused.data["errors"]["switches_remaining"] == 0 and refused.data["errors"]["limit_scope"] == "term"
    # the administrator starts the next semester: the allowance is back
    _roll_term(term, timezone.now())
    assert switch_state(student)["switches_remaining"] == 1
    again = _switch(_login(student, DEVICE_C).data["errors"]["device_switch_token"], DEVICE_C)
    assert again.status_code == 200


def test_semester_boundary_just_before_and_after_rollover(enforced, student, term):  # noqa: F811
    _login(student, DEVICE_A)
    assert _switch(_mismatch_token(student, DEVICE_B), DEVICE_B).status_code == 200
    bound_at = timezone.now() - timedelta(hours=1)
    AccountDevice.objects.filter(user=student, bound_via=AccountDeviceBoundVia.SELF_SWITCH).update(bound_at=bound_at)
    _roll_term(term, bound_at + timedelta(seconds=1))  # the term began a second AFTER the switch: it no longer counts
    assert switch_state(student)["switches_used"] == 0
    _roll_term(term, bound_at - timedelta(seconds=1))  # the term began a second BEFORE the switch: it counts
    assert switch_state(student)["switches_used"] == 1
    _roll_term(term, bound_at)  # exactly at the boundary the switch belongs to the new term
    assert switch_state(student)["switches_used"] == 1


def test_limit_setting_keeps_its_meaning_per_term(enforced, student, term):  # noqa: F811
    enforced.DEVICE_SWITCH_LIMIT_PER_TERM = 2
    _login(student, DEVICE_A)
    assert _switch(_mismatch_token(student, DEVICE_B), DEVICE_B).status_code == 200
    assert _switch(_mismatch_token(student, DEVICE_C), DEVICE_C).status_code == 200
    assert switch_state(student)["switches_remaining"] == 0
    _roll_term(term, timezone.now() + timedelta(seconds=0))
    assert switch_state(student)["switches_remaining"] == 2


def test_term_in_the_future_falls_back_to_the_rolling_window(enforced, student, term):  # noqa: F811
    term["row"].started_at = timezone.now() + timedelta(days=5)
    term["row"].save()
    assert switch_state(student)["limit_scope"] == "rolling"


# -- offline licenses vs device switch / reset --------------------------------------------------------------------------


def _licenses(user, world_university):
    """One file license and one lecture license of the student on a device whose id is arbitrary."""

    StudentProfile.objects.create(
        user=user, university=world_university, verification_status=StudentVerificationStatus.APPROVED
    )
    admin_user = User.objects.create_user(
        full_name="Up", email="up@v.test", phone_number="+963955100077", password="StrongPass123!", role=UserRole.ADMIN
    )
    resource = _ready_file({"admin": admin_user, "university": world_university}, pages=1)
    client = APIClient()
    client.force_authenticate(user)
    headers: dict[str, Any] = {"HTTP_X_PANORAMA_DEVICE_ID": str(uuid.uuid4())}
    response = client.post(f"/api/v1/files/{resource.pk}/offline-license/", **headers)
    assert response.status_code == 201, response.data
    return resource


@pytest.fixture(autouse=True)
def _crypto(settings):
    from cryptography.fernet import Fernet

    settings.FIELD_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")


def test_device_switch_revokes_all_offline_licenses_of_the_old_device(enforced, student):  # noqa: F811
    university = University.objects.create(name="Switch University", code="SU")
    _login(student, DEVICE_A)
    _licenses(student, university)
    assert FileOfflineLicense.objects.filter(revoked_at__isnull=True).count() == 1
    assert _switch(_mismatch_token(student, DEVICE_B), DEVICE_B).status_code == 200
    row = FileOfflineLicense.objects.get()
    assert row.revoked_at is not None and row.revoke_reason == "device_replaced"
    assert AuditLog.objects.filter(action=AuditAction.FILE_OFFLINE_LICENSE_REVOKED).count() == 1
    assert not LectureOfflineLicense.objects.filter(revoked_at__isnull=True).exists()


def test_refused_switch_keeps_licenses(enforced, student):  # noqa: F811
    university = University.objects.create(name="Switch University", code="SU")
    _login(student, DEVICE_A)
    _licenses(student, university)
    _switch(_mismatch_token(student, DEVICE_B), DEVICE_B)
    FileOfflineLicense.objects.update(revoked_at=None, revoke_reason="")  # pretend the new device re-downloaded
    refused = _switch(_login(student, DEVICE_C).data["errors"]["device_switch_token"], DEVICE_C)
    assert refused.status_code == 403
    assert FileOfflineLicense.objects.filter(revoked_at__isnull=True).count() == 1


def test_staff_reset_revokes_offline_licenses_with_actor(enforced, student, admin):  # noqa: F811
    university = University.objects.create(name="Switch University", code="SU")
    _login(student, DEVICE_A)
    _licenses(student, university)
    client = APIClient()
    client.force_authenticate(admin)
    response = client.post(
        f"/api/v1/dashboard/users/{student.pk}/account-devices/reset/", {"reason": "lost"}, format="json"
    )
    assert response.status_code == 200
    row = FileOfflineLicense.objects.get()
    assert row.revoked_at is not None and row.revoke_reason == "revoked_by_staff" and row.revoked_by == admin
    assert AuditLog.objects.filter(action=AuditAction.FILE_OFFLINE_LICENSE_REVOKED, actor=admin).count() == 1
