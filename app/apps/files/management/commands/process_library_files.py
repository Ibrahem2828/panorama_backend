"""Render library files into protected viewer pages (run once after deploying the viewer, then for failures)."""

from django.core.management.base import BaseCommand

from apps.files.models import FileProcessingStatus, FileResource
from apps.files.tasks import process_file_resource


class Command(BaseCommand):
    help = "Process library files that have no viewer pages yet. --failed also retries failures; --inline runs here."

    def add_arguments(self, parser):
        parser.add_argument("--failed", action="store_true", help="Also retry files whose processing failed.")
        parser.add_argument("--inline", action="store_true", help="Process in this process instead of queueing.")
        parser.add_argument("--limit", type=int, default=0, help="Stop after this many files (0 = all).")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        statuses = [FileProcessingStatus.PENDING]
        if options["failed"]:
            statuses.append(FileProcessingStatus.FAILED)
        queryset = FileResource.objects.filter(
            is_deleted=False, is_active=True, processing_status__in=statuses
        ).order_by("id")
        if options["limit"]:
            queryset = queryset[: options["limit"]]
        count = 0
        for resource in queryset:
            count += 1
            if options["dry_run"]:
                continue
            FileResource.objects.filter(pk=resource.pk).update(processing_status=FileProcessingStatus.QUEUED)
            if options["inline"]:
                process_file_resource.apply(args=(resource.pk,))
            else:
                process_file_resource.delay(resource.pk)
        self.stdout.write(f"files={count} inline={options['inline']} dry_run={options['dry_run']}")
