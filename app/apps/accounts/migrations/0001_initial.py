# Generated for Panorama Phase 1.
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

import apps.accounts.managers


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.CreateModel(
            name="User",
            fields=[
                ("password", models.CharField(max_length=128, verbose_name="password")),
                ("last_login", models.DateTimeField(blank=True, null=True, verbose_name="last login")),
                (
                    "is_superuser",
                    models.BooleanField(
                        default=False,
                        help_text="Designates that this user has all permissions without explicitly assigning them.",
                        verbose_name="superuser status",
                    ),
                ),
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("full_name", models.CharField(max_length=255)),
                ("username", models.CharField(blank=True, max_length=150, null=True, unique=True)),
                ("email", models.EmailField(max_length=254, unique=True)),
                ("phone_number", models.CharField(max_length=32, unique=True)),
                (
                    "role",
                    models.CharField(
                        choices=[
                            ("it_support", "IT Support"),
                            ("admin", "Admin"),
                            ("print_staff", "Print Staff"),
                            ("student", "Student"),
                            ("normal_user", "Normal User"),
                        ],
                        default="normal_user",
                        max_length=32,
                    ),
                ),
                ("is_phone_verified", models.BooleanField(default=False)),
                ("is_email_verified", models.BooleanField(default=False)),
                ("is_active", models.BooleanField(default=True)),
                ("is_staff", models.BooleanField(default=False)),
                ("date_joined", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "groups",
                    models.ManyToManyField(
                        blank=True,
                        help_text="The groups this user belongs to. A user will get all permissions granted to each of their groups.",
                        related_name="user_set",
                        related_query_name="user",
                        to="auth.group",
                        verbose_name="groups",
                    ),
                ),
                (
                    "user_permissions",
                    models.ManyToManyField(
                        blank=True,
                        help_text="Specific permissions for this user.",
                        related_name="user_set",
                        related_query_name="user",
                        to="auth.permission",
                        verbose_name="user permissions",
                    ),
                ),
            ],
            options={
                "ordering": ["-date_joined"],
            },
            managers=[
                ("objects", apps.accounts.managers.UserManager()),
            ],
        ),
        migrations.CreateModel(
            name="StudentProfile",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("student_number", models.CharField(blank=True, max_length=64)),
                ("card_image", models.ImageField(blank=True, null=True, upload_to="student_cards/")),
                (
                    "verification_status",
                    models.CharField(
                        choices=[
                            ("incomplete", "Incomplete"),
                            ("pending", "Pending"),
                            ("approved", "Approved"),
                            ("rejected", "Rejected"),
                            ("suspended", "Suspended"),
                        ],
                        default="incomplete",
                        max_length=32,
                    ),
                ),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="student_profile",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="OTPCode",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("phone_number", models.CharField(db_index=True, max_length=32)),
                ("code_hash", models.CharField(max_length=255)),
                (
                    "purpose",
                    models.CharField(
                        choices=[
                            ("register", "Register"),
                            ("verify_phone", "Verify Phone"),
                            ("reset_password", "Reset Password"),
                            ("login", "Login"),
                        ],
                        max_length=32,
                    ),
                ),
                ("expires_at", models.DateTimeField()),
                ("is_used", models.BooleanField(default=False)),
                ("attempts_count", models.PositiveSmallIntegerField(default=0)),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="otp_codes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="user",
            index=models.Index(fields=["email"], name="accounts_us_email_74c8d6_idx"),
        ),
        migrations.AddIndex(
            model_name="user",
            index=models.Index(fields=["phone_number"], name="accounts_us_phone_n_613c4a_idx"),
        ),
        migrations.AddIndex(
            model_name="user",
            index=models.Index(fields=["role"], name="accounts_us_role_1fa9a5_idx"),
        ),
        migrations.AddIndex(
            model_name="studentprofile",
            index=models.Index(fields=["student_number"], name="accounts_st_student_15fe5a_idx"),
        ),
        migrations.AddIndex(
            model_name="studentprofile",
            index=models.Index(fields=["verification_status"], name="accounts_st_verific_9a031c_idx"),
        ),
        migrations.AddIndex(
            model_name="otpcode",
            index=models.Index(fields=["phone_number", "purpose", "is_used"], name="accounts_ot_phone_n_76ae90_idx"),
        ),
        migrations.AddIndex(
            model_name="otpcode",
            index=models.Index(fields=["expires_at"], name="accounts_ot_expires_2f08f4_idx"),
        ),
    ]
