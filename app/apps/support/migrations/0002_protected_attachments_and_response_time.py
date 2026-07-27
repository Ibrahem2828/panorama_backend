# Generated for Panorama Backend v2 protected support attachments.
import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("support", "0001_initial"), ("accounts", "0004_security_rbac_and_email_otp")]
    operations = [
        migrations.AddField(model_name="supportticket", name="last_response_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AlterField(model_name="supportticketmessage", name="attachment", field=models.FileField(blank=True, null=True, upload_to="support/%Y/%m/")),
        migrations.CreateModel(
            name="SupportAttachmentAccessTicket",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)), ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("token", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("expires_at", models.DateTimeField()), ("max_uses", models.PositiveSmallIntegerField(default=4)),
                ("use_count", models.PositiveSmallIntegerField(default=0)), ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("message", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="access_tickets", to="support.supportticketmessage")),
                ("requested_by", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="support_attachment_access_tickets", to=settings.AUTH_USER_MODEL)),
            ], options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(model_name="supportattachmentaccessticket", index=models.Index(fields=["token", "expires_at"], name="support_attachment_ticket_idx")),
    ]
