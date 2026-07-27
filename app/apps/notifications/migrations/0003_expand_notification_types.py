from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("notifications", "0002_devicetoken")]

    operations = [
        migrations.AlterField(
            model_name="notification",
            name="type",
            field=models.CharField(
                choices=[
                    ("verification", "Verification"),
                    ("group", "Group"),
                    ("file", "File"),
                    ("announcement", "Announcement"),
                    ("system", "System"),
                    ("printing", "Printing"),
                    ("support", "Support"),
                    ("feedback", "Feedback"),
                    ("chat", "Chat"),
                ],
                default="system",
                max_length=32,
            ),
        ),
    ]
