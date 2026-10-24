"""Adds every already-verified student to their faculty community (run once after deploying the enrollment flow)."""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import User
from apps.enrollment.services import FacultyCommunityService


class Command(BaseCommand):
    help = "Join all verified students to their faculty community group(s). Idempotent; --dry-run rolls back."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        users = User.objects.filter(
            role=UserRole.STUDENT,
            is_active=True,
            is_deleted=False,
            student_profile__verification_status=StudentVerificationStatus.APPROVED,
        ).select_related("student_profile")
        added = 0
        with transaction.atomic():
            for user in users:
                added += FacultyCommunityService.join(user)
            if options["dry_run"]:
                transaction.set_rollback(True)
        self.stdout.write(f"students={users.count()} memberships_added={added} dry_run={options['dry_run']}")
