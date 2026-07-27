# Generated for Panorama Backend v2 encrypted external channels.
import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("groups", "0002_group_send_messages_permission"), ("accounts", "0004_security_rbac_and_email_otp")]
    operations = [
        migrations.CreateModel(
            name="GroupExternalChannel",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("channel_type", models.CharField(choices=[("whatsapp", "WhatsApp")], max_length=32)),
                ("encrypted_url", models.TextField()),
                ("is_active", models.BooleanField(default=True)),
                ("label", models.CharField(blank=True, max_length=100)),
                ("group", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="external_channels", to="groups.group")),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="updated_group_external_channels", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name="groupexternalchannel",
            constraint=models.UniqueConstraint(fields=("group", "channel_type"), name="unique_group_external_channel"),
        ),
        migrations.AddIndex(
            model_name="groupexternalchannel", index=models.Index(fields=["group", "channel_type", "is_active"], name="groups_ext_channel_active_idx"),
        ),
        migrations.CreateModel(
            name="ExternalChannelAccessTicket",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("token", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("expires_at", models.DateTimeField()),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                ("channel", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="access_tickets", to="groups.groupexternalchannel")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="external_channel_tickets", to=settings.AUTH_USER_MODEL)),
            ], options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="externalchannelaccessticket", index=models.Index(fields=["token", "expires_at"], name="groups_ext_ticket_token_idx"),
        ),
    ]
