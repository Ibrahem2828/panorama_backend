"""Eager revocation of offline file licenses. Validity is re-checked live on every use as well; these hooks keep the
stored state (dashboard, audit) honest and make loss of access take effect at once."""

from __future__ import annotations

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.accounts.models import StudentProfile, User
from apps.groups.models import GroupMembership, GroupMembershipStatus

from . import offline_services as offline
from .models import FileOfflineBlob, FileResource


@receiver(post_save, sender=FileResource, dispatch_uid="files_offline_file_revoke")
def revoke_on_file_change(sender, instance: FileResource, created: bool, **kwargs):
    if created:
        return
    if not instance.viewer_ready:
        offline.revoke_file_licenses(instance.pk, offline.REASON_FILE_UNAVAILABLE)
        return
    live = FileOfflineBlob.objects.filter(file_resource_id=instance.pk, retired_at__isnull=True).first()
    if live is not None and live.source_marker != offline.source_marker(instance):
        offline.retire_blob(live, offline.REASON_FILE_REPLACED)
    # visibility / group / major / year edits may have removed access for some holders
    offline.revoke_lapsed_licenses(file_id=instance.pk)


@receiver(post_save, sender=GroupMembership, dispatch_uid="files_offline_membership_revoke")
def revoke_on_membership_change(sender, instance: GroupMembership, **kwargs):
    if instance.status != GroupMembershipStatus.APPROVED or instance.is_deleted:
        offline.revoke_lapsed_licenses(user_id=instance.user_id)


@receiver(post_save, sender=StudentProfile, dispatch_uid="files_offline_profile_revoke")
def revoke_on_profile_change(sender, instance: StudentProfile, created: bool, **kwargs):
    if not created:  # verification status, major or year may have changed
        offline.revoke_lapsed_licenses(user_id=instance.user_id)


@receiver(post_save, sender=User, dispatch_uid="files_offline_user_revoke")
def revoke_on_account_disabled(sender, instance: User, created: bool, **kwargs):
    if created:
        return
    if not instance.is_active or getattr(instance, "is_deleted", False):
        offline.revoke_user_licenses(instance, offline.REASON_ACCOUNT_DISABLED)
    else:  # e.g. a role change away from student
        offline.revoke_lapsed_licenses(user_id=instance.pk)
