from django.db import migrations

# Mirrors ``categories.category_for`` for rows created before categories existed (kept inline: migrations must not
# import application code that may change).
TYPE_TO_CATEGORY = {
    "chat": "chat",
    "group": "groups",
    "printing": "printing",
    "verification": "verification",
    "support": "support",
}


def backfill(apps, schema_editor):
    Notification = apps.get_model("notifications", "Notification")
    for notification_type, category in TYPE_TO_CATEGORY.items():
        Notification.objects.filter(type=notification_type, category="").update(category=category)
    Notification.objects.filter(category="").update(category="general")


class Migration(migrations.Migration):
    dependencies = [("notifications", "0006_notification_categories_quiet_hours_push_deliveries")]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
