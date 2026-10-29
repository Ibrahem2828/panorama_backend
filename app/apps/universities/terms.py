"""Academic terms: the current semester, "end semester" (remove students from SUBJECT groups) and its undo.

Ending a term never deletes anything: a subject-group membership becomes ``term_ended`` (row kept, ``ended_at`` and the
``rollover`` that did it recorded), so chat history, files and enrollment records stay, and a new enrollment approval can
put the student back. Staff, group admins/moderators and faculty-community groups are never touched. WhatsApp cannot be
automated, so the run also records what staff must do by hand: the phone numbers to remove from each group.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Count
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError

from apps.accounts.choices import UserRole
from apps.accounts.models import StudentProfile
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.groups.models import (
    ExternalChannelAccessTicket,
    ExternalChannelType,
    Group,
    GroupExternalChannel,
    GroupMembership,
    GroupMembershipRole,
    GroupMembershipStatus,
)

from .models import AcademicYear, CurrentTerm, Semester, TermRollover

#: Accounts whose subject-group membership an end of term may remove. Staff accounts are never listed here.
REMOVABLE_ROLES = (UserRole.STUDENT, UserRole.NORMAL_USER)
CHUNK = 500


class TermConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "This term operation cannot run in the current state."
    default_code = "term_conflict"


# -- the current term ---------------------------------------------------------------------------------------------------


def _semester_dict(semester: Semester | None) -> dict | None:
    return {"id": semester.pk, "name": semester.name, "order": semester.order} if semester else None


def active_semesters():
    return Semester.objects.filter(is_deleted=False, is_active=True).order_by("order", "id")


def get_current_term() -> CurrentTerm | None:
    return CurrentTerm.objects.select_related("semester").filter(pk=CurrentTerm.SINGLETON_PK, is_deleted=False).first()


def current_semester() -> Semester | None:
    """The semester the platform is in; before an administrator sets one, the first active semester stands in."""

    term = get_current_term()
    return term.semester if term else active_semesters().first()


def current_semester_id() -> int | None:
    semester = current_semester()
    return semester.pk if semester else None


def other_semester(semester: Semester | None) -> Semester | None:
    """The next semester after ``semester`` (wrapping around), i.e. "the other one" for a two-semester year."""

    if semester is None:
        return None
    others = list(active_semesters().exclude(pk=semester.pk))
    after = [item for item in others if item.order > semester.order]
    return (after or others or [None])[0]


def current_term_payload() -> dict[str, Any]:
    term = get_current_term()
    semester = term.semester if term else active_semesters().first()
    return {
        "semester": _semester_dict(semester),
        "other_semester": _semester_dict(other_semester(semester)),
        "term_started_at": term.started_at if term else None,
        "term_label": term.label if term else "",
    }


def _clean_label(label: str | None) -> str:
    return " ".join(str(label or "").split())[:100]


def _validated_semester(semester_id) -> Semester:
    semester = Semester.objects.filter(pk=semester_id, is_deleted=False, is_active=True).first()
    if semester is None:
        raise ValidationError({"semester": "Choose an active semester."})
    return semester


def set_current_term(*, semester_id, label: str, actor, request=None) -> CurrentTerm:
    semester = _validated_semester(semester_id)
    label = _clean_label(label)
    try:
        with transaction.atomic():
            term = CurrentTerm.objects.select_for_update().filter(pk=CurrentTerm.SINGLETON_PK).first()
            old = {"semester": term.semester_id, "label": term.label} if term else None
            now = timezone.now()
            if term is None:
                term = CurrentTerm.objects.create(
                    pk=CurrentTerm.SINGLETON_PK, semester=semester, label=label, started_at=now, set_by=actor
                )
            else:
                changed = term.semester_id != semester.pk or term.label != label
                term.is_deleted = False
                term.semester = semester
                term.label = label
                term.set_by = actor
                if changed:
                    term.started_at = now
                term.save()
            AuditLogService.log(
                actor=actor,
                action=AuditAction.CURRENT_TERM_CHANGED,
                target=term,
                old_value=old,
                new_value={"semester": semester.pk, "label": label},
                request=request,
                critical=True,
            )
    except IntegrityError as exc:  # two first-time writers raced for the single row
        raise TermConflict("The current term was changed at the same time. Reload and try again.") from exc
    term.refresh_from_db()
    return term


# -- end of term --------------------------------------------------------------------------------------------------------


def removable_memberships():
    """THE set an end of term removes. Preview and the action both read this, so what staff see is what happens."""

    return GroupMembership.objects.filter(
        is_deleted=False,
        status=GroupMembershipStatus.APPROVED,
        role=GroupMembershipRole.MEMBER,
        group__subject__isnull=False,
        group__is_deleted=False,
        user__role__in=REMOVABLE_ROLES,
    )


def _active_whatsapp_group_ids(group_ids) -> set[int]:
    return set(
        GroupExternalChannel.objects.filter(
            group_id__in=list(group_ids),
            channel_type=ExternalChannelType.WHATSAPP,
            is_active=True,
            is_deleted=False,
        ).values_list("group_id", flat=True)
    )


def _group_rows(queryset) -> list[dict[str, Any]]:
    counts = dict(queryset.order_by().values_list("group_id").annotate(n=Count("id")))
    if not counts:
        return []
    groups = Group.objects.filter(pk__in=counts).select_related("subject").order_by("name", "id")
    whatsapp = _active_whatsapp_group_ids(counts)
    return [
        {
            "group_id": group.pk,
            "name": group.name,
            "subject": group.subject.name if group.subject else "",
            "subject_id": group.subject_id,
            "whatsapp_active": group.pk in whatsapp,
            "members_to_remove": counts[group.pk],
        }
        for group in groups
    ]


def preview_end_term() -> dict[str, Any]:
    queryset = removable_memberships()
    return {
        "groups": _group_rows(queryset),
        "total_memberships": queryset.count(),
        "total_students": queryset.values("user_id").distinct().count(),
    }


def _end_term_locked(*, actor, next_semester_id, label, disable_whatsapp_links: bool, request) -> TermRollover:
    next_semester = _validated_semester(next_semester_id)
    label = _clean_label(label)
    term = CurrentTerm.objects.select_for_update().filter(pk=CurrentTerm.SINGLETON_PK).first()
    if term is not None and term.semester_id == next_semester.pk and term.label == label:
        # A second confirm of the same request (double click, retry) lands here once the first one committed.
        raise TermConflict("This term is already the current term; nothing was changed.")

    now = timezone.now()
    ids = list(removable_memberships().select_for_update(of=("self",)).values_list("pk", flat=True))
    queryset = GroupMembership.objects.filter(pk__in=ids)
    groups = _group_rows(queryset) if ids else []
    affected_group_ids = [row["group_id"] for row in groups]
    whatsapp_ids = {row["group_id"] for row in groups if row["whatsapp_active"]}
    student_count = queryset.values("user_id").distinct().count() if ids else 0
    for row in groups:
        row["whatsapp_disabled"] = bool(disable_whatsapp_links and row["group_id"] in whatsapp_ids)

    rollover = TermRollover.objects.create(
        performed_by=actor,
        from_semester=term.semester if term else None,
        to_semester=next_semester,
        from_label=term.label if term else "",
        to_label=label,
        disable_whatsapp_links=disable_whatsapp_links,
        memberships_removed=len(ids),
        students_affected=student_count,
        snapshot=groups,
        previous_started_at=term.started_at if term else None,
    )
    for start in range(0, len(ids), CHUNK):
        GroupMembership.objects.filter(pk__in=ids[start : start + CHUNK]).update(
            status=GroupMembershipStatus.TERM_ENDED, ended_at=now, rollover=rollover, updated_at=now
        )
    if disable_whatsapp_links and whatsapp_ids:
        channels = GroupExternalChannel.objects.filter(
            group_id__in=whatsapp_ids, channel_type=ExternalChannelType.WHATSAPP, is_active=True, is_deleted=False
        )
        channel_ids = list(channels.values_list("pk", flat=True))
        channels.update(is_active=False, updated_by=actor, updated_at=now)
        # Open tickets die with the link, even though a disabled channel already fails ``is_valid``.
        ExternalChannelAccessTicket.objects.filter(
            channel_id__in=channel_ids, used_at__isnull=True, expires_at__gt=now
        ).update(expires_at=now, updated_at=now)

    if term is None:
        CurrentTerm.objects.update_or_create(
            pk=CurrentTerm.SINGLETON_PK,
            defaults={
                "semester": next_semester,
                "label": label,
                "started_at": now,
                "set_by": actor,
                "is_deleted": False,
            },
        )
    else:
        term.semester = next_semester
        term.label = label
        term.started_at = now
        term.set_by = actor
        term.save()
    StudentProfile.objects.filter(is_deleted=False, user__role=UserRole.STUDENT).update(
        needs_term_confirmation=True, updated_at=now
    )
    AuditLogService.log(
        actor=actor,
        action=AuditAction.TERM_ENDED,
        target=rollover,
        old_value={"semester": rollover.from_semester_id, "label": rollover.from_label},
        new_value={
            "semester": next_semester.pk,
            "label": label,
            "memberships_removed": len(ids),
            "students": student_count,
            "groups": len(affected_group_ids),
            "whatsapp_links_disabled": len(whatsapp_ids) if disable_whatsapp_links else 0,
        },
        request=request,
        critical=True,
    )
    return rollover


def end_term(*, actor, next_semester_id, label: str = "", disable_whatsapp_links: bool = True, request=None):
    """Atomically end the term. Returns the rollover. A concurrent or repeated confirm raises ``TermConflict``."""

    try:
        with transaction.atomic():
            return _end_term_locked(
                actor=actor,
                next_semester_id=next_semester_id,
                label=label,
                disable_whatsapp_links=disable_whatsapp_links,
                request=request,
            )
    except IntegrityError as exc:  # two first-ever runs raced to create the single current-term row
        raise TermConflict("The term was changed at the same time. Reload and try again.") from exc


def latest_rollover() -> TermRollover | None:
    return TermRollover.objects.filter(is_deleted=False).order_by("-created_at", "-id").first()


def undo_end_term(rollover_id: int, *, actor, request=None) -> dict[str, Any]:
    with transaction.atomic():
        # Same lock as ending a term, so an undo and an end-term can never interleave.
        term = CurrentTerm.objects.select_for_update().filter(pk=CurrentTerm.SINGLETON_PK).first()
        latest = TermRollover.objects.select_for_update().filter(is_deleted=False).order_by("-created_at", "-id").first()
        rollover = TermRollover.objects.select_for_update().filter(pk=rollover_id, is_deleted=False).first()
        if rollover is None:
            from django.http import Http404

            raise Http404
        if rollover.undone_at is not None:
            raise TermConflict("This end of term was already undone.")
        if latest is None or latest.pk != rollover.pk:
            raise TermConflict("Only the most recent end of term can be undone.")

        now = timezone.now()
        ids = list(
            GroupMembership.objects.select_for_update(of=("self",))
            .filter(rollover=rollover, status=GroupMembershipStatus.TERM_ENDED, is_deleted=False)
            .values_list("pk", flat=True)
        )
        for start in range(0, len(ids), CHUNK):
            GroupMembership.objects.filter(pk__in=ids[start : start + CHUNK]).update(
                status=GroupMembershipStatus.APPROVED, ended_at=None, rollover=None, updated_at=now
            )
        # Put the previous term back so the app, the groups and the "did you move up?" prompt agree again.
        if rollover.from_semester_id and Semester.objects.filter(pk=rollover.from_semester_id).exists():
            if term is None:
                CurrentTerm.objects.create(
                    pk=CurrentTerm.SINGLETON_PK,
                    semester_id=rollover.from_semester_id,
                    label=rollover.from_label,
                    started_at=rollover.previous_started_at or now,
                    set_by=actor,
                )
            else:
                term.semester_id = rollover.from_semester_id
                term.label = rollover.from_label
                term.started_at = rollover.previous_started_at or now
                term.set_by = actor
                term.save()
        elif term is not None:
            CurrentTerm.objects.filter(pk=term.pk).delete()  # there was no current term before this run
        StudentProfile.objects.filter(is_deleted=False, needs_term_confirmation=True).update(
            needs_term_confirmation=False, updated_at=now
        )
        rollover.undone_at = now
        rollover.undone_by = actor
        rollover.save(update_fields=["undone_at", "undone_by", "updated_at"])
        AuditLogService.log(
            actor=actor,
            action=AuditAction.TERM_ROLLOVER_UNDONE,
            target=rollover,
            new_value={"memberships_restored": len(ids), "semester": rollover.from_semester_id},
            request=request,
            critical=True,
        )
    return {"rollover_id": rollover.pk, "restored": len(ids)}


# -- the manual WhatsApp clean-up list ------------------------------------------------------------------------------------


def whatsapp_removal_list(rollover: TermRollover) -> dict[str, Any]:
    """Per group, who must be removed from the WhatsApp group by hand. Only memberships this run is still responsible for."""

    if rollover.undone_at is not None:
        raise TermConflict("This end of term was undone; there is nobody to remove.")
    memberships = (
        GroupMembership.objects.filter(rollover=rollover, status=GroupMembershipStatus.TERM_ENDED, is_deleted=False)
        .select_related("group", "user")
        .order_by("group__name", "group_id", "user__full_name", "user_id")
    )
    groups: dict[int, dict[str, Any]] = {}
    for membership in memberships:
        entry = groups.setdefault(
            membership.group_id, {"group_id": membership.group_id, "name": membership.group.name, "members": []}
        )
        entry["members"].append({"full_name": membership.user.full_name, "phone_number": membership.user.phone_number})
    return {"groups": list(groups.values())}


def csv_safe(value: Any) -> str:
    """Spreadsheet formula injection: a text cell starting with = + - @ is read as a formula, so it is quoted."""

    text = "" if value is None else str(value)
    return "'" + text if text[:1] in {"=", "+", "-", "@", "\t", "\r"} else text


def csv_phone(value: Any) -> str:
    """Phone numbers legitimately start with ``+``; anything that is not plainly a number is treated as text."""

    text = "" if value is None else str(value).strip()
    return text if text and all(ch.isdigit() or ch in "+ -()" for ch in text) else csv_safe(text)


def removal_list_csv(data: dict[str, Any]) -> str:
    buffer = io.StringIO()
    buffer.write("\ufeff")  # Excel opens UTF-8 (Arabic) correctly only with a BOM
    writer = csv.writer(buffer)
    writer.writerow(["group", "name", "phone"])
    for group in data["groups"]:
        for member in group["members"]:
            writer.writerow([csv_safe(group["name"]), csv_safe(member["full_name"]), csv_phone(member["phone_number"])])
    return buffer.getvalue()


# -- the student's own answer ---------------------------------------------------------------------------------------------


@transaction.atomic
def confirm_student_term(user, *, promoted: bool, academic_year_id=None, semester_id=None, request=None):
    profile = StudentProfile.objects.select_for_update().filter(user=user, is_deleted=False).first()
    if profile is None:
        raise ValidationError({"detail": "Only students can answer this."})
    if not profile.needs_term_confirmation:
        raise TermConflict("There is no pending question about your term.")
    old = {"academic_year": profile.academic_year_id, "semester": profile.semester_id}
    if promoted:
        if academic_year_id is None and semester_id is None:
            raise ValidationError({"academic_year": "Choose the year (or semester) you moved to."})
        if academic_year_id is not None:
            year = AcademicYear.objects.filter(pk=academic_year_id, is_deleted=False, is_active=True).first()
            if year is None:
                raise ValidationError({"academic_year": "Choose an active academic year."})
            profile.academic_year = year
        if semester_id is not None:
            profile.semester = _validated_semester(semester_id)
    profile.needs_term_confirmation = False
    profile.save()
    AuditLogService.log(
        actor=user,
        action=AuditAction.STUDENT_TERM_CONFIRMED,
        target=profile,
        old_value=old,
        new_value={
            "promoted": promoted,
            "academic_year": profile.academic_year_id,
            "semester": profile.semester_id,
        },
        request=request,
        critical=True,
    )
    return profile
