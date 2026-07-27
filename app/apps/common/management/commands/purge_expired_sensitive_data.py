from __future__ import annotations

from datetime import timedelta
import logging

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import OTPCode, StudentProfile, UserPermissionOverride
from apps.chat.models import MessageAttachmentAccessTicket
from apps.feedback.models import FeedbackPromptEvent
from apps.files.models import FileAccessTicket
from apps.groups.models import ExternalChannelAccessTicket
from apps.printing.models import PrintItemAccessTicket
from apps.support.models import SupportAttachmentAccessTicket
from apps.verification.models import VerificationCardAccessTicket, VerificationRequest, VerificationStatus


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Purge expired OTPs/access tickets and delete retained student-card images according to policy."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report counts without deleting data.")

    @transaction.atomic
    def handle(self, *args, **options):
        now = timezone.now()
        dry_run = options["dry_run"]
        counts: dict[str, int] = {}

        otp_before = now - timedelta(days=getattr(settings, "OTP_RETENTION_DAYS", 7))
        counts["otp_codes"] = self._purge_queryset(
            OTPCode.objects.filter(expires_at__lt=otp_before),
            dry_run,
        )

        ticket_before = now - timedelta(hours=getattr(settings, "ACCESS_TICKET_RETENTION_HOURS", 24))
        ticket_models = {
            "verification_card_tickets": VerificationCardAccessTicket,
            "file_access_tickets": FileAccessTicket,
            "external_channel_tickets": ExternalChannelAccessTicket,
            "print_item_tickets": PrintItemAccessTicket,
            "support_attachment_tickets": SupportAttachmentAccessTicket,
            "chat_attachment_tickets": MessageAttachmentAccessTicket,
        }
        for label, model in ticket_models.items():
            counts[label] = self._purge_queryset(model.objects.filter(expires_at__lt=ticket_before), dry_run)

        expired_overrides = UserPermissionOverride.objects.filter(expires_at__lt=now, is_deleted=False)
        counts["permission_overrides"] = expired_overrides.count()
        if not dry_run:
            expired_overrides.update(is_deleted=True, deleted_at=now, updated_at=now)

        feedback_before = now - timedelta(days=getattr(settings, "FEEDBACK_PROMPT_EVENT_RETENTION_DAYS", 365))
        counts["feedback_prompt_events"] = self._purge_queryset(
            FeedbackPromptEvent.objects.filter(created_at__lt=feedback_before),
            dry_run,
        )

        card_before = now - timedelta(days=getattr(settings, "VERIFICATION_CARD_RETENTION_DAYS", 90))
        terminal_statuses = [
            VerificationStatus.APPROVED,
            VerificationStatus.REJECTED,
            VerificationStatus.CANCELLED,
        ]
        verifications = VerificationRequest.objects.filter(
            status__in=terminal_statuses,
            reviewed_at__lt=card_before,
        ).exclude(card_image="")
        counts["verification_card_images"] = verifications.count()
        if not dry_run:
            for verification in verifications.iterator():
                self._delete_field_file(verification.card_image)
                verification.card_image = ""
                verification.save(update_fields=["card_image", "updated_at"])

        legacy_profiles = StudentProfile.objects.filter(
            verification_reviewed_at__lt=card_before,
        ).exclude(card_image="")
        counts["legacy_profile_card_images"] = legacy_profiles.count()
        if not dry_run:
            for profile in legacy_profiles.iterator():
                self._delete_field_file(profile.card_image)
                profile.card_image = ""
                profile.save(update_fields=["card_image", "updated_at"])

        prefix = "DRY RUN - " if dry_run else ""
        self.stdout.write(self.style.SUCCESS(f"{prefix}sensitive-data retention cleanup complete"))
        for label, count in counts.items():
            self.stdout.write(f"{label}: {count}")
        if dry_run:
            transaction.set_rollback(True)

    @staticmethod
    def _purge_queryset(queryset, dry_run: bool) -> int:
        count = queryset.count()
        if not dry_run and count:
            queryset.delete()
        return count

    @staticmethod
    def _delete_field_file(field_file) -> None:
        try:
            if field_file and field_file.name:
                field_file.delete(save=False)
        except Exception:
            # Continue the retention job, but preserve the failure for operations and alerting.
            logger.exception("Unable to delete retained sensitive file from storage", extra={"file_name": getattr(field_file, "name", "")})
