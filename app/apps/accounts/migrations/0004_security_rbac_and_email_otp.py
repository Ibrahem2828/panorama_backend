# Generated for Panorama Backend v2 production hardening.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0003_studentprofile_enrollment_year_code_and_more")]

    operations = [
        migrations.AddField(
            model_name="user", name="last_password_change_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="user", name="role",
            field=models.CharField(
                choices=[
                    ("it_support", "IT Support"), ("admin", "Admin"),
                    ("print_staff", "Print Staff"), ("support_staff", "Support Staff"),
                    ("content_manager", "Content Manager"), ("student", "Student"),
                    ("normal_user", "Normal User"),
                ], default="normal_user", max_length=32,
            ),
        ),
        migrations.AddIndex(
            model_name="user", index=models.Index(fields=["is_active", "role"], name="accounts_user_active_role_idx"),
        ),
        migrations.AlterField(
            model_name="otpcode", name="phone_number",
            field=models.CharField(blank=True, db_index=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="otpcode", name="email",
            field=models.EmailField(blank=True, db_index=True, default="", max_length=254),
        ),
        migrations.AddField(
            model_name="otpcode", name="delivery_channel",
            field=models.CharField(choices=[("email", "Email"), ("phone", "Phone")], default="email", max_length=16),
        ),
        migrations.AddField(
            model_name="otpcode", name="locked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="otpcode", name="purpose",
            field=models.CharField(
                choices=[
                    ("register", "Register"), ("verify_phone", "Verify Phone"),
                    ("verify_email", "Verify Email"), ("reset_password", "Reset Password"),
                    ("login", "Login"),
                ], max_length=32,
            ),
        ),
        migrations.AddIndex(
            model_name="otpcode", index=models.Index(fields=["email", "purpose", "is_used"], name="accounts_otp_email_purpose_idx"),
        ),
        migrations.AddIndex(
            model_name="otpcode", index=models.Index(fields=["delivery_channel", "purpose", "created_at"], name="accounts_otp_channel_created"),
        ),
        migrations.CreateModel(
            name="UserPermissionOverride",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("permission_code", models.CharField(max_length=128)),
                ("effect", models.CharField(choices=[("allow", "Allow"), ("deny", "Deny")], max_length=16)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("reason", models.CharField(blank=True, max_length=255)),
                ("granted_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="granted_permission_overrides", to=settings.AUTH_USER_MODEL)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="permission_overrides", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["user_id", "permission_code"]},
        ),
        migrations.AddConstraint(
            model_name="userpermissionoverride",
            constraint=models.UniqueConstraint(fields=("user", "permission_code"), name="unique_user_permission_override"),
        ),
        migrations.AddIndex(
            model_name="userpermissionoverride", index=models.Index(fields=["user", "permission_code"], name="accounts_permission_user_code"),
        ),
        migrations.AddIndex(
            model_name="userpermissionoverride", index=models.Index(fields=["expires_at"], name="accounts_permission_expiry_idx"),
        ),
    ]
