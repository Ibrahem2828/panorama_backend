from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.notifications.models import NotificationType
from apps.notifications.services import NotificationService

from .models import Group, GroupMembership, GroupMembershipStatus


def is_student_eligible_for_group(user, group: Group) -> bool:
    if user.role != UserRole.STUDENT:
        return False
    profile = getattr(user, "student_profile", None)
    if not profile or profile.verification_status != StudentVerificationStatus.APPROVED:
        return False
    if group.university_id and profile.university_id != group.university_id:
        return False
    if group.faculty_id and profile.faculty_id != group.faculty_id:
        return False
    if group.major_id and profile.major_id != group.major_id:
        return False
    if group.academic_year_id and profile.academic_year_id != group.academic_year_id:
        return False
    if group.semester_id and profile.semester_id != group.semester_id:
        return False
    return True


class GroupMembershipService:
    @staticmethod
    def join(user, group: Group) -> GroupMembership:
        if not group.is_active or group.is_deleted:
            raise ValidationError("Group is not active.")
        if not is_student_eligible_for_group(user, group):
            raise ValidationError("You are not eligible to join this group.")

        membership = GroupMembership.objects.filter(group=group, user=user).first()
        if membership and membership.status == GroupMembershipStatus.BLOCKED:
            raise ValidationError("You are blocked from this group.")
        if membership and membership.status in {GroupMembershipStatus.PENDING, GroupMembershipStatus.APPROVED}:
            raise ValidationError("You already have an active membership for this group.")

        status = GroupMembershipStatus.PENDING if group.requires_approval else GroupMembershipStatus.APPROVED
        joined_at = timezone.now() if status == GroupMembershipStatus.APPROVED else None
        if membership:
            membership.status = status
            membership.joined_at = joined_at
            membership.save()
            return membership
        return GroupMembership.objects.create(group=group, user=user, status=status, joined_at=joined_at)

    @staticmethod
    def leave(user, group: Group) -> GroupMembership:
        membership = GroupMembership.objects.get(group=group, user=user, status=GroupMembershipStatus.APPROVED)
        membership.status = GroupMembershipStatus.LEFT
        membership.save(update_fields=["status", "updated_at"])
        return membership

    @staticmethod
    def review(membership: GroupMembership, reviewer, status: str) -> GroupMembership:
        if status == GroupMembershipStatus.APPROVED:
            membership.approve(reviewer)
            title = "Group request approved"
            body = f"Your request to join {membership.group.name} was approved."
        else:
            membership.status = status
            membership.reviewed_by = reviewer
            membership.reviewed_at = timezone.now()
            membership.save()
            title = "Group membership updated"
            body = f"Your membership request for {membership.group.name} was {status}."
        NotificationService.create_notification(
            membership.user,
            title=title,
            body=body,
            type=NotificationType.GROUP,
            data={"group_id": membership.group_id, "membership_id": membership.id, "status": status},
        )
        return membership
