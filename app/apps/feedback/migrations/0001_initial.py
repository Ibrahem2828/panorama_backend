# Generated for Panorama Backend v2 feedback and rating system.
import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(
            name="FeedbackPromptPolicy",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)), ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("context", models.CharField(choices=[("app", "Whole App"), ("onboarding", "Onboarding"), ("registration", "Registration"), ("login", "Login"), ("verification", "Student Verification"), ("home", "Home"), ("subject", "Subject"), ("group", "Group"), ("chat", "Chat"), ("file", "File Viewer"), ("printing", "Printing"), ("notification", "Notifications"), ("support", "Support"), ("profile", "Profile"), ("other", "Other")], max_length=32)),
                ("action_key", models.CharField(blank=True, max_length=100)), ("title", models.CharField(max_length=200)),
                ("question", models.CharField(max_length=500)), ("is_active", models.BooleanField(default=True)),
                ("minimum_app_version", models.CharField(blank=True, max_length=32)),
                ("cooldown_days", models.PositiveSmallIntegerField(default=30)),
                ("sample_percent", models.PositiveSmallIntegerField(default=100, validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(100)])),
                ("allow_comment", models.BooleanField(default=True)), ("allow_suggestion", models.BooleanField(default=True)),
            ], options={"ordering": ["context", "action_key"]},
        ),
        migrations.AddConstraint(model_name="feedbackpromptpolicy", constraint=models.UniqueConstraint(fields=("context", "action_key"), name="unique_feedback_prompt_policy")),
        migrations.CreateModel(
            name="AppFeedback",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)), ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("kind", models.CharField(choices=[("rating", "Rating"), ("suggestion", "Suggestion"), ("bug", "Bug Report"), ("complaint", "Complaint"), ("compliment", "Compliment")], max_length=32)),
                ("context", models.CharField(choices=[("app", "Whole App"), ("onboarding", "Onboarding"), ("registration", "Registration"), ("login", "Login"), ("verification", "Student Verification"), ("home", "Home"), ("subject", "Subject"), ("group", "Group"), ("chat", "Chat"), ("file", "File Viewer"), ("printing", "Printing"), ("notification", "Notifications"), ("support", "Support"), ("profile", "Profile"), ("other", "Other")], max_length=32)),
                ("action_key", models.CharField(blank=True, max_length=100)), ("object_type", models.CharField(blank=True, max_length=100)),
                ("object_id", models.CharField(blank=True, max_length=64)),
                ("rating", models.PositiveSmallIntegerField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(5)])),
                ("title", models.CharField(blank=True, max_length=200)), ("comment", models.TextField(blank=True)),
                ("suggestion", models.TextField(blank=True)),
                ("status", models.CharField(choices=[("new", "New"), ("reviewing", "Reviewing"), ("planned", "Planned"), ("in_progress", "In Progress"), ("resolved", "Resolved"), ("rejected", "Rejected"), ("duplicate", "Duplicate")], default="new", max_length=32)),
                ("priority", models.CharField(choices=[("low", "Low"), ("normal", "Normal"), ("high", "High"), ("critical", "Critical")], default="normal", max_length=16)),
                ("internal_notes", models.TextField(blank=True)), ("resolution_message", models.TextField(blank=True)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)), ("app_version", models.CharField(blank=True, max_length=32)),
                ("build_number", models.CharField(blank=True, max_length=32)), ("platform", models.CharField(blank=True, max_length=32)),
                ("locale", models.CharField(blank=True, max_length=16)), ("device_model", models.CharField(blank=True, max_length=100)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("assigned_to", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="assigned_feedback", to=settings.AUTH_USER_MODEL)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="app_feedback", to=settings.AUTH_USER_MODEL)),
            ], options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(model_name="appfeedback", index=models.Index(fields=["context", "action_key", "created_at"], name="feedback_context_action_idx")),
        migrations.AddIndex(model_name="appfeedback", index=models.Index(fields=["kind", "status", "priority"], name="feedback_kind_status_idx")),
        migrations.AddIndex(model_name="appfeedback", index=models.Index(fields=["rating", "created_at"], name="feedback_rating_created_idx")),
        migrations.AddIndex(model_name="appfeedback", index=models.Index(fields=["user", "created_at"], name="feedback_user_created_idx")),
        migrations.AddConstraint(
            model_name="appfeedback", constraint=models.UniqueConstraint(
                condition=models.Q(("is_deleted", False), ("kind", "rating")),
                fields=("user", "context", "action_key", "object_type", "object_id", "app_version"),
                name="unique_rating_per_action_object_version",
            ),
        ),
        migrations.CreateModel(
            name="FeedbackVote",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)), ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("feedback", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="votes", to="feedback.appfeedback")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="feedback_votes", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(model_name="feedbackvote", constraint=models.UniqueConstraint(fields=("feedback", "user"), name="unique_feedback_vote")),
        migrations.AddIndex(model_name="feedbackvote", index=models.Index(fields=["feedback", "created_at"], name="feedback_vote_created_idx")),
        migrations.CreateModel(
            name="FeedbackPromptEvent",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)), ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("event", models.CharField(choices=[("shown", "Shown"), ("dismissed", "Dismissed"), ("submitted", "Submitted")], max_length=16)),
                ("app_version", models.CharField(blank=True, max_length=32)),
                ("policy", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="events", to="feedback.feedbackpromptpolicy")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="feedback_prompt_events", to=settings.AUTH_USER_MODEL)),
            ], options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(model_name="feedbackpromptevent", index=models.Index(fields=["user", "policy", "event", "created_at"], name="feedback_prompt_event_idx")),
    ]
