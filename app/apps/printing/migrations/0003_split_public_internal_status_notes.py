from django.db import migrations, models


def copy_legacy_notes(apps, schema_editor):
    History = apps.get_model("printing", "PrintOrderStatusHistory")
    for row in History.objects.exclude(note="").iterator():
        row.internal_note = row.note
        row.save(update_fields=["internal_note"])


class Migration(migrations.Migration):
    dependencies = [("printing", "0002_pricing_engine_and_protected_items")]

    operations = [
        migrations.AddField(
            model_name="printorderstatushistory",
            name="public_note",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="printorderstatushistory",
            name="internal_note",
            field=models.TextField(blank=True),
        ),
        migrations.RunPython(copy_legacy_notes, migrations.RunPython.noop),
        migrations.RemoveField(model_name="printorderstatushistory", name="note"),
    ]
