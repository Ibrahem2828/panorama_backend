"""Makes sure the construction-engineering faculty has its community group with the given WhatsApp invite link.

Idempotent: re-running changes nothing once the group and link are in place. It never creates a faculty and never touches
other faculties; when the faculty cannot be identified unambiguously it stops and says so. ``--dry-run`` writes nothing.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.choices import UserRole
from apps.accounts.models import User
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.crypto import decrypt_text
from apps.groups.models import ExternalChannelType, Group, GroupExternalChannel
from apps.groups.services import ExternalChannelService
from apps.universities.models import Faculty

DEFAULT_URL = "https://chat.whatsapp.com/EG1Jyzndt1WAAFsPVlYc08"
#: The faculty is called «هندسة الإنشاء والتشييد» by the user and «هندسة تكنولوجيا البناء والتشييد» in the seed data.
NAME_MARKER = "التشييد"


class Command(BaseCommand):
    help = "Create/update the community group (and its WhatsApp link) of the construction engineering faculty."

    def add_arguments(self, parser):
        parser.add_argument("--url", default=DEFAULT_URL, help="Official https WhatsApp invite link.")
        parser.add_argument("--university-code", default="SPU")
        parser.add_argument("--faculty-id", type=int, help="Use this faculty instead of matching by name.")
        parser.add_argument("--created-by-email", help="Owner of a newly created group (default: first active admin).")
        parser.add_argument("--dry-run", action="store_true", help="Report what would change and roll everything back.")

    def _faculty(self, options) -> Faculty:
        faculties = Faculty.objects.filter(is_deleted=False, is_active=True)
        if options["faculty_id"]:
            faculty = faculties.filter(pk=options["faculty_id"]).first()
            if faculty is None:
                raise CommandError(f"No active faculty with id {options['faculty_id']}.")
            return faculty
        matches = list(
            faculties.filter(university__code=options["university_code"].upper(), name__contains=NAME_MARKER)
        )
        if len(matches) == 1:
            return matches[0]
        found = ", ".join(f"{f.id}:{f.name}" for f in matches) or "none"
        raise CommandError(
            f"Expected exactly one active faculty with «{NAME_MARKER}» in its name, found: {found}. "
            "Nothing was changed; pass --faculty-id to choose."
        )

    def _owner(self, email: str | None) -> User:
        users = User.objects.filter(is_active=True, is_deleted=False)
        owner = (
            users.filter(email__iexact=email).first()
            if email
            else users.filter(role=UserRole.ADMIN).order_by("id").first()
        )
        if owner is None:
            raise CommandError("No admin user found to own the new group; pass --created-by-email.")
        return owner

    def handle(self, *args, **options):
        url = ExternalChannelService.validate_whatsapp_url(options["url"])
        with transaction.atomic():
            faculty = self._faculty(options)
            groups = Group.objects.filter(
                faculty=faculty,
                subject__isnull=True,
                major__isnull=True,
                academic_year__isnull=True,
                semester__isnull=True,
                is_deleted=False,
            ).order_by("-is_active", "id")
            group = groups.first()
            owner = group.created_by if group else self._owner(options["created_by_email"])
            if group is None:
                group = Group.objects.create(
                    name=f"مجتمع {faculty.name}",
                    description="المجتمع العام لطلاب الكلية.",
                    university=faculty.university,
                    faculty=faculty,
                    created_by=owner,
                    requires_approval=False,
                )
                group_state = "created"
            elif not group.is_active:
                group.is_active = True
                group.save(update_fields=["is_active", "updated_at"])
                group_state = "reactivated"
            else:
                group_state = "existing"

            channel = GroupExternalChannel.objects.filter(
                group=group, channel_type=ExternalChannelType.WHATSAPP, is_deleted=False
            ).first()
            if channel and channel.is_active and decrypt_text(channel.encrypted_url) == url:
                link_state = "unchanged"
            else:
                link_state = "updated" if channel else "created"
                ExternalChannelService.set_whatsapp_channel(
                    group, url, actor=owner, label=(channel.label if channel else "") or "مجتمع الكلية"
                )
                AuditLogService.log(
                    actor=owner,
                    action=AuditAction.GROUP_EXTERNAL_CHANNEL_UPDATED,
                    target=group,
                    new_value={"channel_type": ExternalChannelType.WHATSAPP, "is_active": True, "source": "command"},
                )
            if options["dry_run"]:
                transaction.set_rollback(True)
        self.stdout.write(
            f"faculty={faculty.id} ({faculty.name}) group={group.id} group_{group_state} link_{link_state} "
            f"dry_run={options['dry_run']}"
        )
        if group_state == "created" and not options["dry_run"]:
            self.stdout.write(
                "New group: run `manage.py backfill_faculty_communities` so already-verified students of this faculty join it."
            )
