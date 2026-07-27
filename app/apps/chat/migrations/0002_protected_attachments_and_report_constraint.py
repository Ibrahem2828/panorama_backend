import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
from django.db.models import Count, Min


def deduplicate_reports(apps, schema_editor):
    MessageReport = apps.get_model("chat", "MessageReport")
    duplicates = (
        MessageReport.objects.values("message_id", "reported_by_id")
        .annotate(keep_id=Min("id"), total=Count("id"))
        .filter(total__gt=1)
    )
    for row in duplicates.iterator():
        MessageReport.objects.filter(
            message_id=row["message_id"],
            reported_by_id=row["reported_by_id"],
        ).exclude(id=row["keep_id"]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_security_rbac_and_email_otp"),
        ("chat", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(deduplicate_reports, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="messagereport",
            constraint=models.UniqueConstraint(
                fields=("message", "reported_by"),
                name="unique_message_report_per_user",
            ),
        ),
        migrations.CreateModel(
            name="MessageAttachmentAccessTicket",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("token", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("expires_at", models.DateTimeField()),
                ("max_uses", models.PositiveSmallIntegerField(default=8)),
                ("use_count", models.PositiveSmallIntegerField(default=0)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                (
                    "message",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="attachment_access_tickets",
                        to="chat.message",
                    ),
                ),
                (
                    "requested_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chat_attachment_access_tickets",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="messageattachmentaccessticket",
            index=models.Index(fields=["token", "expires_at"], name="chat_messag_token_3efcbb_idx"),
        ),
    ]
