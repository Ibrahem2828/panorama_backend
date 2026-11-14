"""Phone verification with a staff-sent WhatsApp code (apps.accounts.phone_verification)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.choices import PhoneVerificationStatus as S
from apps.accounts.choices import UserRole
from apps.accounts.models import PhoneVerificationRequest, User
from apps.accounts.phone_verification import PhoneVerificationRefused, hash_code, normalize_phone
from apps.audit.models import AuditAction, AuditLog

pytestmark = pytest.mark.django_db

PASSWORD = "StrongPass123!"
URL = "/api/v1/auth/phone-verification/"


def _user(email, phone, role=UserRole.STUDENT, **extra):
    return User.objects.create_user(
        full_name=f"User {email}",
        email=email,
        phone_number=phone,
        password=PASSWORD,
        role=role,
        is_email_verified=True,
        **extra,
    )


@pytest.fixture
def student():
    return _user("pv.student@example.test", "+963955200001")


@pytest.fixture
def staff():
    return _user("pv.staff@example.test", "+963955200002", UserRole.ADMIN)


def _as(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


def _request(student, phone="0912345678", password=PASSWORD):
    return _as(student).post(URL, {"phone_number": phone, "password": password}, format="json")


def _claim(staff, request_id):
    return _as(staff).post(f"/api/v1/dashboard/phone-verifications/{request_id}/claim/")


def _send(staff, request_id):
    return _as(staff).post(f"/api/v1/dashboard/phone-verifications/{request_id}/mark-sent/")


def _full_flow(student, staff, phone="0912345678"):
    created = _request(student, phone)
    assert created.status_code == 201, created.data
    rid = created.data["data"]["id"]
    code = _claim(staff, rid).data["data"]["code"]
    assert _send(staff, rid).status_code == 200
    return rid, code


# -- normalization ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "0912345678",
        "912345678",
        "963912345678",
        "+963912345678",
        "00963912345678",
        "+963 912 345 678",
        "+9630912345678",
        "٠٩١٢٣٤٥٦٧٨",
        "0912-345-678",
        "(+963) 912.345.678",
    ],
)
def test_syrian_numbers_normalize_to_e164(raw):
    assert normalize_phone(raw) == "+963912345678"


def test_other_countries_need_an_international_prefix():
    assert normalize_phone("+905321234567") == "+905321234567"
    assert normalize_phone("00905321234567") == "+905321234567"


@pytest.mark.parametrize(
    "raw", ["", "abc", "091234", "0812345678", "+96391234", "+0123456789", "12345", "+1" + "2" * 15]
)
def test_invalid_numbers_are_rejected(raw):
    with pytest.raises(PhoneVerificationRefused) as info:
        normalize_phone(raw)
    assert info.value.code == "PHONE_NUMBER_INVALID"


# -- student: create / status / cancel --------------------------------------------------------------------------------


def test_create_requires_the_right_password_and_a_valid_phone(student):
    wrong = _request(student, password="nope")
    assert wrong.status_code == 400 and wrong.data["code"] == "PASSWORD_INCORRECT"
    bad = _request(student, phone="12")
    assert bad.status_code == 400 and bad.data["code"] == "PHONE_NUMBER_INVALID"
    assert not PhoneVerificationRequest.objects.exists()


def test_create_stores_the_normalized_number_and_reports_status(student):
    response = _request(student, "0912 345 678")
    assert response.status_code == 201 and response.data["code"] == "PHONE_VERIFICATION_REQUESTED"
    data = response.data["data"]
    assert data["phone_number"] == "+963912345678" and data["status"] == S.PENDING_STAFF
    assert "code" not in data and "code_hash" not in data
    current = _as(student).get(URL + "current/")
    assert current.data["data"]["request"]["id"] == data["id"]
    assert AuditLog.objects.filter(action=AuditAction.PHONE_VERIFICATION_REQUESTED, actor=student).exists()


def test_current_is_null_before_any_request(student):
    assert _as(student).get(URL + "current/").data["data"]["request"] is None


def test_only_one_open_request_per_student(student):
    assert _request(student).status_code == 201
    second = _request(student, "0922345678")
    assert second.status_code == 409 and second.data["code"] == "PHONE_VERIFICATION_ALREADY_OPEN"
    assert PhoneVerificationRequest.objects.count() == 1


def test_creation_is_throttled_per_student(student):
    for _ in range(3):
        _request(student, password="nope")  # wrong passwords count too
    assert _request(student).status_code == 429


def test_cancel_closes_the_open_request_and_allows_a_new_one(student):
    rid = _request(student).data["data"]["id"]
    cancelled = _as(student).post(URL + "cancel/")
    assert cancelled.status_code == 200 and cancelled.data["data"]["status"] == S.CANCELLED
    assert _as(student).post(URL + "cancel/").status_code == 404
    assert _request(student, "0922345678").status_code == 201
    assert PhoneVerificationRequest.objects.get(pk=rid).status == S.CANCELLED
    assert AuditLog.objects.filter(action=AuditAction.PHONE_VERIFICATION_CANCELLED).exists()


def test_only_students_use_the_student_endpoints(staff):
    assert _as(staff).post(URL, {"phone_number": "0912345678", "password": PASSWORD}, format="json").status_code == 403
    assert APIClient().get(URL + "current/").status_code == 401


def test_number_used_by_another_account_is_a_clear_conflict(student):
    _user("other@example.test", "+963912345678")
    response = _request(student)
    assert response.status_code == 409 and response.data["code"] == "PHONE_NUMBER_IN_USE"
    _user("other2@example.test", "0933345678")  # stored in the local spelling
    assert _request(student, "+963933345678").data["code"] == "PHONE_NUMBER_IN_USE"


def test_already_verified_number_is_refused(student):
    student.phone_number = "+963912345678"
    student.is_phone_verified = True
    student.save()
    assert _request(student).data["code"] == "PHONE_ALREADY_VERIFIED"


# -- staff queue ------------------------------------------------------------------------------------------------------


def test_staff_queue_lists_requests_with_phone_but_never_the_code(student, staff):
    rid = _request(student).data["data"]["id"]
    _claim(staff, rid)
    listing = _as(staff).get("/api/v1/dashboard/phone-verifications/?status=pending_staff")
    assert listing.status_code == 200
    row = listing.data["data"]["results"][0]
    assert row["phone_number"] == "+963912345678" and row["code_generated"] is True
    assert row["claimed_by_name"] == staff.full_name
    detail = _as(staff).get(f"/api/v1/dashboard/phone-verifications/{rid}/")
    assert "code" not in detail.data["data"] and "code_hash" not in str(detail.data)
    assert _as(staff).get("/api/v1/dashboard/phone-verifications/?search=912345").data["data"]["count"] == 1


def test_queue_is_permissioned(student):
    assert _as(student).get("/api/v1/dashboard/phone-verifications/").status_code == 403
    printer = _user("pv.print@example.test", "+963955200003", UserRole.PRINT_STAFF)
    assert _as(printer).get("/api/v1/dashboard/phone-verifications/").status_code == 403
    assert _as(printer).post("/api/v1/dashboard/phone-verifications/1/claim/").status_code == 403
    assert APIClient().get("/api/v1/dashboard/phone-verifications/").status_code == 401


def test_staff_are_notified_of_new_requests(student, staff):
    _request(student)
    assert staff.notifications.filter(data__kind="phone_verification_request").exists()


def test_claim_returns_a_six_digit_code_once_and_stores_only_a_keyed_hash(student, staff):
    rid = _request(student).data["data"]["id"]
    response = _claim(staff, rid)
    assert response.status_code == 200 and response["Cache-Control"].startswith("no-store")
    code = response.data["data"]["code"]
    assert len(code) == 6 and code.isdigit()
    stored = PhoneVerificationRequest.objects.get(pk=rid)
    assert stored.code_hash == hash_code(rid, code) and code not in stored.code_hash
    assert stored.status == S.PENDING_STAFF and stored.claimed_by == staff
    # the plain code is in no audit row
    for row in AuditLog.objects.all():
        assert code not in str(row.old_value) + str(row.new_value)
    again = _as(staff).get(f"/api/v1/dashboard/phone-verifications/{rid}/")
    assert "code" not in again.data["data"]


def test_claiming_again_issues_a_new_code_and_other_staff_wait(student, staff):
    rid = _request(student).data["data"]["id"]
    first = _claim(staff, rid).data["data"]["code"]
    second = _claim(staff, rid).data["data"]["code"]
    stored = PhoneVerificationRequest.objects.get(pk=rid)
    assert stored.code_hash == hash_code(rid, second)
    assert first == second or stored.code_hash != hash_code(rid, first)
    colleague = _user("pv.staff2@example.test", "+963955200004", UserRole.ADMIN)
    blocked = _claim(colleague, rid)
    assert blocked.status_code == 409 and blocked.data["code"] == "PHONE_VERIFICATION_CLAIMED_BY_OTHER"
    assert _send(colleague, rid).status_code == 409
    PhoneVerificationRequest.objects.filter(pk=rid).update(claimed_at=timezone.now() - timedelta(minutes=30))
    assert _claim(colleague, rid).status_code == 200  # the hold lapsed


def test_mark_sent_needs_a_claim_and_starts_the_ten_minute_window(student, staff):
    rid = _request(student).data["data"]["id"]
    early = _send(staff, rid)
    assert early.status_code == 409 and early.data["code"] == "PHONE_VERIFICATION_NOT_CLAIMED"
    _claim(staff, rid)
    before = timezone.now()
    sent = _send(staff, rid)
    assert sent.status_code == 200 and sent.data["data"]["status"] == S.CODE_SENT
    stored = PhoneVerificationRequest.objects.get(pk=rid)
    assert stored.expires_at is not None
    assert stored.expires_at - before >= timedelta(minutes=9, seconds=55)
    assert stored.expires_at - before <= timedelta(minutes=10, seconds=5)
    assert student.notifications.filter(data__kind="phone_verification", data__status="code_sent").exists()
    assert _claim(staff, rid).status_code == 409  # no longer pending


def test_staff_can_reject_with_a_reason(student, staff):
    rid = _request(student).data["data"]["id"]
    assert _as(staff).post(f"/api/v1/dashboard/phone-verifications/{rid}/reject/", {}, format="json").status_code == 400
    response = _as(staff).post(
        f"/api/v1/dashboard/phone-verifications/{rid}/reject/", {"reason": "الرقم غير صحيح"}, format="json"
    )
    assert response.status_code == 200 and response.data["data"]["status"] == S.REJECTED
    assert student.notifications.filter(data__status="rejected").exists()
    assert AuditLog.objects.filter(action=AuditAction.PHONE_VERIFICATION_REJECTED, actor=staff).exists()
    assert _request(student).status_code == 201  # a rejected request does not block a new one


# -- confirming -------------------------------------------------------------------------------------------------------


def test_correct_code_verifies_the_phone(student, staff):
    rid, code = _full_flow(student, staff)
    response = _as(student).post(URL + "confirm/", {"code": code}, format="json")
    assert response.status_code == 200 and response.data["code"] == "PHONE_VERIFIED"
    student.refresh_from_db()
    assert student.is_phone_verified is True and student.phone_number == "+963912345678"
    stored = PhoneVerificationRequest.objects.get(pk=rid)
    assert stored.status == S.VERIFIED and stored.verified_at and stored.code_hash == ""
    assert AuditLog.objects.filter(action=AuditAction.PHONE_VERIFICATION_VERIFIED).exists()
    assert student.notifications.filter(data__status="verified").exists()
    # the code cannot be replayed
    assert _as(student).post(URL + "confirm/", {"code": code}, format="json").status_code == 404


def test_wrong_code_counts_attempts_and_locks_after_five(student, staff):
    rid, code = _full_flow(student, staff)
    wrong = "000000" if code != "000000" else "111111"
    client = _as(student)
    for left in (4, 3, 2, 1):
        response = client.post(URL + "confirm/", {"code": wrong}, format="json")
        assert response.status_code == 400 and response.data["code"] == "PHONE_CODE_INVALID"
        assert response.data["errors"]["attempts_remaining"] == left
    last = client.post(URL + "confirm/", {"code": wrong}, format="json")
    assert last.data["code"] == "PHONE_CODE_ATTEMPTS_EXCEEDED" and last.data["errors"]["attempts_remaining"] == 0
    stored = PhoneVerificationRequest.objects.get(pk=rid)
    assert stored.status == S.EXPIRED and stored.attempts == 5  # persisted despite the error responses
    # even the right code is dead now
    assert client.post(URL + "confirm/", {"code": code}, format="json").status_code == 404
    student.refresh_from_db()
    assert student.is_phone_verified is False
    assert AuditLog.objects.filter(action=AuditAction.PHONE_VERIFICATION_FAILED).count() == 5


def test_expired_code_is_refused(student, staff):
    rid, code = _full_flow(student, staff)
    PhoneVerificationRequest.objects.filter(pk=rid).update(expires_at=timezone.now() - timedelta(seconds=1))
    response = _as(student).post(URL + "confirm/", {"code": code}, format="json")
    assert response.status_code == 404  # swept to "expired" before the check: no open request any more
    assert PhoneVerificationRequest.objects.get(pk=rid).status == S.EXPIRED
    assert student.notifications.filter(data__status="expired").exists()
    student.refresh_from_db()
    assert student.is_phone_verified is False


def test_code_before_staff_sent_it_is_refused(student, staff):
    rid = _request(student).data["data"]["id"]
    _claim(staff, rid)
    response = _as(student).post(URL + "confirm/", {"code": "123456"}, format="json")
    assert response.status_code == 409 and response.data["code"] == "PHONE_CODE_NOT_SENT"


def test_code_must_be_six_digits(student, staff):
    _full_flow(student, staff)
    assert _as(student).post(URL + "confirm/", {"code": "12ab56"}, format="json").status_code == 400
    assert _as(student).post(URL + "confirm/", {"code": "12345"}, format="json").status_code == 400


def test_another_students_code_does_not_work(student, staff):
    other = _user("pv.other@example.test", "+963955200005")
    rid, code = _full_flow(student, staff)
    assert _as(other).post(URL + "confirm/", {"code": code}, format="json").status_code == 404


def test_pending_requests_expire_after_the_configured_hours(student, staff, settings):
    rid = _request(student).data["data"]["id"]
    PhoneVerificationRequest.objects.filter(pk=rid).update(
        created_at=timezone.now() - timedelta(hours=settings.PHONE_VERIFICATION_PENDING_TTL_HOURS + 1)
    )
    row = _as(staff).get("/api/v1/dashboard/phone-verifications/").data["data"]["results"][0]
    assert row["status"] == S.EXPIRED
    assert _request(student).status_code == 201


def test_number_taken_in_the_meantime_is_a_conflict_at_confirmation(student, staff):
    rid, code = _full_flow(student, staff)
    _user("late@example.test", "+963912345678")
    response = _as(student).post(URL + "confirm/", {"code": code}, format="json")
    assert response.status_code == 409 and response.data["code"] == "PHONE_NUMBER_IN_USE"
    student.refresh_from_db()
    assert student.is_phone_verified is False and student.phone_number == "+963955200001"
    assert PhoneVerificationRequest.objects.get(pk=rid).status == S.REJECTED


def test_verified_number_can_be_used_to_log_in(student, staff):
    _, code = _full_flow(student, staff)
    _as(student).post(URL + "confirm/", {"code": code}, format="json")
    login = APIClient().post(
        "/api/v1/auth/login/", {"identifier": "+963912345678", "password": PASSWORD}, format="json"
    )
    assert login.status_code == 200


def test_codes_are_random_six_digit_strings():
    from apps.accounts.phone_verification import generate_code

    codes = {generate_code() for _ in range(200)}
    assert all(len(c) == 6 and c.isdigit() for c in codes) and len(codes) > 150
