from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.common.reference_import import ReferenceDataError, import_reference_data, load_json_file


class Command(BaseCommand):
    help = (
        "Import production reference data (universities, faculties, majors, subjects, academic years, semesters, "
        "print pricing, pickup locations, terms/privacy versions) from a JSON file. Validated, idempotent, runs in "
        "one transaction, never deletes and never creates users. Use --dry-run first."
    )

    def add_arguments(self, parser):
        parser.add_argument("file", help="Path to the JSON file (see docs/reference-data.example.json)")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate and report what would change, then roll everything back.",
        )

    def handle(self, *args, **options):
        dry_run = bool(options["dry_run"])
        try:
            document = load_json_file(options["file"])
            report = import_reference_data(document, dry_run=dry_run)
        except ReferenceDataError as exc:
            for error in exc.errors:
                self.stderr.write(self.style.ERROR(f"ERROR {error}"))
            raise CommandError(
                f"Reference data NOT imported ({len(exc.errors)} problem(s)); the database was not changed."
            ) from exc
        mode = "DRY RUN (nothing was written)" if dry_run else "IMPORTED"
        self.stdout.write(self.style.SUCCESS(f"Reference data {mode}"))
        for line in report.lines():
            self.stdout.write(f"  {line}")
        for warning in report.warnings:
            self.stdout.write(self.style.WARNING(f"WARNING {warning}"))
        if not dry_run and report.total_changes == 0:
            self.stdout.write("No changes: the database already matches the file.")
