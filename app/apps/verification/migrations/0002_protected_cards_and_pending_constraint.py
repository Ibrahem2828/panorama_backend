# Generated for Panorama Backend v2 protected verification cards.
import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def resolve_duplicate_pending(apps, schema_editor):
    VerificationRequest = apps.get_model("verification", "VerificationRequest")
    user_ids = (
        VerificationRequest.objects.filter(status="pending", is_deleted=False)
        .values_list("user_id", flat=True).distinct()
    )
    for user_id in user_ids:
        pending = list(
            VerificationRequest.objects.filter(user_id=user_id, status="pending", is_deleted=False)
            .order_by("-created_at", "-id")
        )
        if len(pending) > 1:
            VerificationRequest.objects.filter(pk__in=[item.pk for item in pending[1:]]).update(status="cancelled")


class Migration(migrations.Migration):
    dependencies = [("verification", "0001_initial"), ("accounts", "0004_security_rbac_and_email_otp")]
    operations = [
        migrations.RunPython(resolve_duplicate_pending, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="verificationrequest",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_deleted", False), ("status", "pending")),
                fields=("user",), name="unique_pending_verification_per_user",
            ),
        ),
        migrations.CreateModel(
            name="VerificationCardAccessTicket",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("token", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("expires_at", models.DateTimeField()),
                ("max_uses", models.PositiveSmallIntegerField(default=3)),
                ("use_count", models.PositiveSmallIntegerField(default=0)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("requested_by", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="verification_card_tickets", to=settings.AUTH_USER_MODEL)),
                ("verification", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="card_access_tickets", to="verification.verificationrequest")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="verificationcardaccessticket",
            index=models.Index(fields=["token", "expires_at"], name="verification_card_token_idx"),
        ),
    ]
