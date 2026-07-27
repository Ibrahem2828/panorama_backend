# Generated for Panorama Backend v2 protected file delivery.
import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("files", "0001_initial"), ("accounts", "0004_security_rbac_and_email_otp")]
    operations = [
        migrations.AddField(model_name="fileresource", name="pages_count", field=models.PositiveIntegerField(blank=True, null=True)),
        migrations.AddField(model_name="fileresource", name="sha256", field=models.CharField(blank=True, db_index=True, max_length=64)),
        migrations.CreateModel(
            name="FileAccessTicket",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("token", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("purpose", models.CharField(choices=[("view", "View"), ("print_preview", "Print Preview"), ("admin_review", "Admin Review")], default="view", max_length=32)),
                ("expires_at", models.DateTimeField()),
                ("max_uses", models.PositiveSmallIntegerField(default=8)),
                ("use_count", models.PositiveSmallIntegerField(default=0)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("issued_ip_hash", models.CharField(blank=True, max_length=64)),
                ("file_resource", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="access_tickets", to="files.fileresource")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="file_access_tickets", to=settings.AUTH_USER_MODEL)),
            ], options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(model_name="fileaccessticket", index=models.Index(fields=["token", "expires_at"], name="files_ticket_token_expiry_idx")),
        migrations.AddIndex(model_name="fileaccessticket", index=models.Index(fields=["user", "file_resource", "created_at"], name="files_ticket_user_file_idx")),
    ]
