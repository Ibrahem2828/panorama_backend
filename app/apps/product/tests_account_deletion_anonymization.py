from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone

from apps.accounts.choices import UserRole
from apps.accounts.models import OTPCode, StudentProfile, User
from apps.audit.models import AuditAction, AuditLog
from apps.chat.models import Message, MessageReport
from apps.feedback.models import AppFeedback, FeedbackAITriage, FeedbackContext, FeedbackKind
from apps.files.models import FileResource
from apps.groups.models import Group
from apps.notifications.models import DeviceToken
from apps.printing.models import PrintOrder, PrintOrderItem, PrintOrderStatus
from apps.product.models import AccountDeletionRequest, AccountDeletionStatus
from apps.product.services import AccountDeletionService
from apps.product.tasks import execute_due_account_deletions
from apps.support.models import SupportTicket, SupportTicketMessage
from apps.universities.models import AcademicYear, Faculty, Major, Semester, University
from apps.verification.models import VerificationRequest

EMAIL = "erase.me@example.test"
PHONE = "+963944000111"
STUDENT_NUMBER = "2150094"
SECRET = "my-private-secret-text"
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _make_user(email: str, phone: str, name: str) -> User:
    return User.objects.create_user(
        full_name=name, email=email, phone_number=phone, password="StrongPass123!", role=UserRole.STUDENT
    )


def _populate(user: User, group: Group, academic: dict, tag: str) -> dict:
    """Create one row of every personal-data category for ``user``; return stored file names."""

    files: dict[str, str] = {}

    def store(key: str, ext: str = "pdf") -> ContentFile:
        content = ContentFile(b"%PDF-1.4 " + tag.encode(), name=f"{tag}-{key}.{ext}")
        files[key] = ""
        return content

    profile, _ = StudentProfile.objects.update_or_create(
        user=user,
        defaults={
            "student_number": STUDENT_NUMBER if tag == "a" else "999",
            "faculty_code_from_student_number": "2",
            "enrollment_year_code": "15",
            "enrollment_year_full": 2015,
            "student_serial_number": "0094",
        },
    )
    profile.card_image.save(f"{tag}-profile.png", ContentFile(PNG_BYTES), save=True)
    files["profile_card"] = profile.card_image.name

    verification = VerificationRequest.objects.create(
        user=user,
        student_profile=profile,
        university=academic["university"],
        faculty=academic["faculty"],
        major=academic["major"],
        academic_year=academic["year"],
        semester=academic["semester"],
        student_number=STUDENT_NUMBER if tag == "a" else "999",
        card_image=ContentFile(PNG_BYTES, name=f"{tag}-verif.png"),
        rejection_reason=f"{SECRET}-{tag}-rejection",
        admin_note=f"{SECRET}-{tag}-note",
    )
    files["verification_card"] = verification.card_image.name

    OTPCode.objects.create(
        user=user,
        email=user.email,
        phone_number=user.phone_number,
        code_hash="x",
        purpose="login",
        expires_at=timezone.now() + timedelta(minutes=5),
    )
    # Pre-login OTP rows have no user link and are matched by contact value only.
    OTPCode.objects.create(
        user=None, email=user.email, code_hash="y", purpose="login", expires_at=timezone.now() + timedelta(minutes=5)
    )
    OTPCode.objects.create(
        user=None,
        phone_number=user.phone_number,
        code_hash="z",
        purpose="login",
        expires_at=timezone.now() + timedelta(minutes=5),
    )
    DeviceToken.objects.create(user=user, token=f"fcm-token-{tag}", platform="android")

    feedback = AppFeedback.objects.create(
        user=user,
        kind=FeedbackKind.SUGGESTION,
        context=FeedbackContext.values[0],
        title=f"{SECRET}-{tag}-title",
        comment=f"{SECRET}-{tag}-comment",
        suggestion=f"{SECRET}-{tag}-suggestion",
        metadata={"note": f"{SECRET}-{tag}-meta"},
        device_model=f"Phone-{tag}",
    )
    FeedbackAITriage.objects.create(feedback=feedback, redacted_text=f"{SECRET}-{tag}-redacted")

    ticket = SupportTicket.objects.create(user=user, subject=f"{SECRET}-{tag}-subject")
    ticket_message = SupportTicketMessage.objects.create(
        ticket=ticket,
        sender=user,
        message=f"{SECRET}-{tag}-support",
        attachment=store("support"),
    )
    files["support"] = ticket_message.attachment.name

    chat = Message.objects.create(group=group, sender=user, content=f"{SECRET}-{tag}-chat", attachment=store("chat"))
    files["chat"] = chat.attachment.name
    MessageReport.objects.create(message=chat, reported_by=user, reason=f"{SECRET}-{tag}-report")

    resource = FileResource.objects.create(
        title=f"{SECRET}-{tag}-resource", description=f"{SECRET}-{tag}-desc", file=store("resource"), uploaded_by=user
    )
    files["resource"] = resource.file.name

    return {
        "files": files,
        "order_ids": [
            _make_order(user, tag, PrintOrderStatus.SUBMITTED, files, "open"),
            _make_order(user, tag, PrintOrderStatus.DELIVERED, files, "done"),
            _make_order(user, tag, PrintOrderStatus.PRINTING, files, "printing"),
        ],
    }


def _make_order(user, tag, status, files, key) -> int:
    order = PrintOrder.objects.create(user=user, status=status, user_notes=f"{SECRET}-{tag}-printnote-{key}")
    item = PrintOrderItem.objects.create(
        order=order,
        uploaded_file=ContentFile(b"%PDF-1.4 print " + tag.encode(), name=f"{tag}-{key}-print.pdf"),
        original_file_name=f"{SECRET}-{tag}-{key}.pdf",
        pages_count=3,
    )
    files[f"print_{key}"] = item.uploaded_file.name
    return order.id


@pytest.fixture
def academic(db):
    university = University.objects.create(name="Damascus University", code="DU")
    faculty = Faculty.objects.create(university=university, name="Engineering", code="2")
    major = Major.objects.create(faculty=faculty, name="Software Engineering", code="SWE")
    return {
        "university": university,
        "faculty": faculty,
        "major": major,
        "year": AcademicYear.objects.create(name="First Year", order=1),
        "semester": Semester.objects.create(name="First Semester", order=1),
    }


@pytest.fixture
def group(db, academic):
    owner = _make_user("owner@example.test", "+963944000999", "Owner")
    return Group.objects.create(name="Chat", university=academic["university"], created_by=owner)


def _schedule_due(user: User) -> AccountDeletionRequest:
    deletion = AccountDeletionService.request(user, reason="bye")
    AccountDeletionRequest.objects.filter(pk=deletion.pk).update(scheduled_for=timezone.now() - timedelta(seconds=1))
    return deletion


def _all_text_dump() -> str:
    """Concatenate every text column of the personal-data tables for a leak search."""

    parts: list[str] = []
    for model in (
        StudentProfile,
        VerificationRequest,
        OTPCode,
        DeviceToken,
        AppFeedback,
        FeedbackAITriage,
        SupportTicket,
        SupportTicketMessage,
        Message,
        MessageReport,
        FileResource,
        PrintOrder,
        PrintOrderItem,
    ):
        for row in model.objects.all().values():
            parts.append(repr(row))
    return "\n".join(parts)


@pytest.mark.django_db
def test_execute_due_erases_personal_data_and_is_idempotent(group, academic, django_capture_on_commit_callbacks):
    victim = _make_user(EMAIL, PHONE, "Erase Me")
    bystander = _make_user("keep.me@example.test", "+963944000222", "Keep Me")
    erased = _populate(victim, group, academic, "a")
    kept = _populate(bystander, group, academic, "b")
    AuditLog.objects.create(
        actor=victim, action=AuditAction.ACCOUNT_DELETION_REQUESTED, ip_address="10.1.2.3", user_agent="UA-victim"
    )
    AuditLog.objects.create(
        actor=bystander, action=AuditAction.ACCOUNT_DELETION_REQUESTED, ip_address="10.9.9.9", user_agent="UA-other"
    )
    for name in list(erased["files"].values()) + list(kept["files"].values()):
        assert default_storage.exists(name), name

    deletion = _schedule_due(victim)
    with django_capture_on_commit_callbacks(execute=True):
        assert AccountDeletionService.execute_due() == 1

    deletion.refresh_from_db()
    assert deletion.status == AccountDeletionStatus.COMPLETED

    # No original identifier or free text survives in any personal-data table.
    dump = _all_text_dump()
    for needle in (EMAIL, PHONE, STUDENT_NUMBER, "fcm-token-a", f"{SECRET}-a-"):
        assert needle not in dump, needle
    for name in erased["files"].values():
        assert not default_storage.exists(name), name

    # Business rows survive, stripped of personal content.
    assert StudentProfile.objects.filter(user=victim, student_number="").exists()
    assert VerificationRequest.objects.filter(user=victim, student_number="").count() == 1
    assert OTPCode.objects.filter(user=victim).count() == 0
    assert DeviceToken.objects.filter(user=victim).count() == 0
    assert SupportTicket.objects.filter(user=victim).count() == 1
    assert Message.objects.filter(sender=victim).count() == 1
    assert PrintOrder.objects.filter(user=victim).count() == 3
    statuses = dict(PrintOrder.objects.filter(user=victim).values_list("id", "status"))
    assert statuses[erased["order_ids"][0]] == PrintOrderStatus.CANCELLED
    assert statuses[erased["order_ids"][1]] == PrintOrderStatus.DELIVERED
    assert statuses[erased["order_ids"][2]] == PrintOrderStatus.CANCELLED
    assert FileResource.objects.filter(uploaded_by=victim).count() == 1
    assert AuditLog.objects.filter(actor=victim).exists()
    assert not AuditLog.objects.filter(actor=victim).exclude(ip_address=None).exists()
    assert not AuditLog.objects.filter(actor=victim).exclude(user_agent="").exists()

    # Completion audit row carries counts only.
    completed = AuditLog.objects.get(action=AuditAction.ACCOUNT_DELETION_COMPLETED)
    assert completed.new_value is not None
    assert completed.new_value["anonymized"]["otp_codes"] == 3
    assert EMAIL not in repr(completed.new_value)

    # The other user is untouched.
    assert StudentProfile.objects.get(user=bystander).student_number == "999"
    assert VerificationRequest.objects.get(user=bystander).student_number == "999"
    assert OTPCode.objects.filter(user=bystander).exists()
    assert DeviceToken.objects.filter(token="fcm-token-b").exists()
    assert AppFeedback.objects.get(user=bystander).comment == f"{SECRET}-b-comment"
    assert FeedbackAITriage.objects.get(feedback__user=bystander).redacted_text == f"{SECRET}-b-redacted"
    assert SupportTicket.objects.get(user=bystander).subject == f"{SECRET}-b-subject"
    assert Message.objects.get(sender=bystander).content == f"{SECRET}-b-chat"
    assert FileResource.objects.get(uploaded_by=bystander).title == f"{SECRET}-b-resource"
    assert PrintOrder.objects.filter(user=bystander, user_notes__contains=f"{SECRET}-b-").count() == 3
    assert PrintOrder.objects.filter(user=bystander, status=PrintOrderStatus.SUBMITTED).count() == 1
    assert AuditLog.objects.get(actor=bystander, user_agent="UA-other").ip_address == "10.9.9.9"
    for name in kept["files"].values():
        assert default_storage.exists(name), name

    # A second run is a no-op.
    before = AuditLog.objects.filter(action=AuditAction.ACCOUNT_DELETION_COMPLETED).count()
    assert AccountDeletionService.execute_due() == 0
    assert AuditLog.objects.filter(action=AuditAction.ACCOUNT_DELETION_COMPLETED).count() == before


@pytest.mark.django_db
def test_missing_file_in_storage_does_not_break_deletion(group, academic, django_capture_on_commit_callbacks):
    victim = _make_user(EMAIL, PHONE, "Erase Me")
    erased = _populate(victim, group, academic, "a")
    default_storage.delete(erased["files"]["chat"])
    default_storage.delete(erased["files"]["verification_card"])

    _schedule_due(victim)
    with django_capture_on_commit_callbacks(execute=True):
        assert AccountDeletionService.execute_due() == 1
    victim.refresh_from_db()
    assert victim.is_deleted is True


@pytest.mark.django_db
def test_files_survive_when_transaction_rolls_back(group, academic):
    from django.db import transaction

    from apps.product.anonymization import anonymize_user_data

    victim = _make_user(EMAIL, PHONE, "Erase Me")
    erased = _populate(victim, group, academic, "a")

    class Boom(Exception):
        pass

    with pytest.raises(Boom), transaction.atomic():
        anonymize_user_data(victim, original_email=EMAIL, original_phone=PHONE)
        raise Boom

    # on_commit callbacks were discarded with the rolled-back savepoint.
    for name in erased["files"].values():
        assert default_storage.exists(name), name
    assert VerificationRequest.objects.get(user=victim).card_image.name == erased["files"]["verification_card"]


@pytest.mark.django_db
def test_repeated_request_does_not_extend_grace_period():
    user = _make_user(EMAIL, PHONE, "Erase Me")
    first = AccountDeletionService.request(user, reason="first")
    original = first.scheduled_for
    AccountDeletionRequest.objects.filter(pk=first.pk).update(scheduled_for=original - timedelta(days=5))
    second = AccountDeletionService.request(user, reason="second")
    second.refresh_from_db()
    assert second.pk == first.pk
    assert second.scheduled_for == original - timedelta(days=5)
    assert second.reason == "first"
    assert AccountDeletionRequest.objects.filter(user=user).count() == 1

    # After cancelling, a fresh request restarts the grace period.
    AccountDeletionService.cancel(user)
    third = AccountDeletionService.request(user, reason="again")
    assert third.status == AccountDeletionStatus.REQUESTED
    assert third.scheduled_for > original - timedelta(days=5)


@pytest.mark.django_db
def test_celery_task_runs_due_deletions():
    user = _make_user(EMAIL, PHONE, "Erase Me")
    _schedule_due(user)
    assert execute_due_account_deletions() == 1
    user.refresh_from_db()
    assert user.is_deleted is True
    assert execute_due_account_deletions() == 0
