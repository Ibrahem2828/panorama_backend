from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.models import OTPCode


class Command(BaseCommand):
    help = "Delete expired or already-used OTP records older than the retention window."

    def add_arguments(self, parser):
        parser.add_argument(
            "--retention-days",
            type=int,
            default=1,
            help="Keep expired or used OTP records newer than this many days.",
        )

    def handle(self, *args, **options):
        retention_days = max(options["retention_days"], 0)
        cutoff = timezone.now() - timezone.timedelta(days=retention_days)
        deleted_count, _ = OTPCode.objects.filter(
            created_at__lt=cutoff,
        ).filter(
            is_used=True,
        ).delete()
        expired_count, _ = OTPCode.objects.filter(
            expires_at__lt=cutoff,
            is_used=False,
        ).delete()
        total = deleted_count + expired_count
        self.stdout.write(self.style.SUCCESS(f"Deleted {total} expired or used OTP records."))
