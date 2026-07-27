# Generated for Panorama Backend v2 complete feedback contexts.
from django.db import migrations, models


CONTEXT_CHOICES = [
    ("app", "Whole App"), ("onboarding", "Onboarding"), ("registration", "Registration"),
    ("login", "Login"), ("verification", "Student Verification"), ("home", "Home"),
    ("subject", "Subject"), ("group", "Group"), ("chat", "Chat"),
    ("file", "File Viewer"), ("printing", "Printing"), ("notification", "Notifications"),
    ("support", "Support"), ("profile", "Profile"), ("search", "Search"),
    ("announcement", "Announcement"), ("settings", "Settings"),
    ("external_channel", "External Channel"), ("other", "Other"),
]


class Migration(migrations.Migration):
    dependencies = [("feedback", "0001_initial")]
    operations = [
        migrations.AlterField(model_name="feedbackpromptpolicy", name="context", field=models.CharField(choices=CONTEXT_CHOICES, max_length=32)),
        migrations.AlterField(model_name="appfeedback", name="context", field=models.CharField(choices=CONTEXT_CHOICES, max_length=32)),
    ]
