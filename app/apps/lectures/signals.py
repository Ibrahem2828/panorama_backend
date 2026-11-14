"""Eager revocation of offline licenses. Validity is also re-checked live on every use, so these hooks keep the stored
state (and the dashboard/audit trail) honest rather than being the only line of defence."""

from __future__ import annotations

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.accounts.models import User
from apps.groups.models import GroupMembership, GroupMembershipStatus

from . import offline_services as offline
from .models import Lecture, LectureOfflineBlob


@receiver(post_save, sender=GroupMembership, dispatch_uid="lectures_offline_membership_revoke")
def revoke_on_membership_change(sender, instance: GroupMembership, **kwargs):
    if instance.status != GroupMembershipStatus.APPROVED or instance.is_deleted:
        offline.revoke_membership_licenses(instance.user_id, instance.group_id)


@receiver(post_save, sender=Lecture, dispatch_uid="lectures_offline_lecture_revoke")
def revoke_on_lecture_change(sender, instance: Lecture, created: bool, **kwargs):
    if created:
        return
    if not instance.is_ready_for_students:
        offline.revoke_lecture_licenses(instance.pk, offline.REASON_LECTURE_UNAVAILABLE)
        return
    live = LectureOfflineBlob.objects.filter(lecture_id=instance.pk, retired_at__isnull=True).first()
    if live is not None and live.source_name != instance.viewer_pdf.name:
        offline.retire_blob(live, offline.REASON_LECTURE_REPLACED)


@receiver(post_save, sender=User, dispatch_uid="lectures_offline_user_revoke")
def revoke_on_account_disabled(sender, instance: User, created: bool, **kwargs):
    if not created and (not instance.is_active or getattr(instance, "is_deleted", False)):
        offline.revoke_user_licenses(instance, offline.REASON_ACCOUNT_DISABLED)
