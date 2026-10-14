from django.db import migrations, models
from django.db.models import Count


def _duplicates(queryset):
    return list(
        queryset.exclude(original_sha256="")
        .values("subject_id", "original_sha256")
        .annotate(total=Count("id"))
        .filter(total__gt=1)
        .order_by("subject_id")[:10]
    )


def assert_no_live_duplicates(apps, schema_editor):
    """Forward guard: refuse to loosen the constraint on top of inconsistent data."""

    Lecture = apps.get_model("lectures", "Lecture")
    duplicates = _duplicates(Lecture.objects.filter(is_deleted=False))
    if duplicates:
        raise RuntimeError(
            "Cannot migrate lectures: live lectures share the same (subject, original_sha256): "
            f"{duplicates}. Soft-delete or fix the duplicates and run the migration again."
        )


def assert_no_duplicates_including_deleted(apps, schema_editor):
    """Reverse guard: the old constraint also covers soft-deleted rows, so re-upload duplicates must be resolved."""

    Lecture = apps.get_model("lectures", "Lecture")
    duplicates = _duplicates(Lecture.objects.all())
    if duplicates:
        raise RuntimeError(
            "Cannot reverse lectures.0002: a document was re-uploaded after a lecture was deleted, so "
            f"(subject, original_sha256) is no longer unique across all rows: {duplicates}. "
            "Hard-delete the superseded soft-deleted rows first."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("lectures", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(assert_no_live_duplicates, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="lecture",
            name="lectures_subject_original_hash_uniq",
        ),
        migrations.AddConstraint(
            model_name="lecture",
            constraint=models.UniqueConstraint(
                condition=models.Q(models.Q(("original_sha256", ""), _negated=True), ("is_deleted", False)),
                fields=("subject", "original_sha256"),
                name="lectures_subject_original_hash_uniq",
            ),
        ),
        # No-op forward; FIRST when reversing, so the guard runs before the old constraint is re-created.
        migrations.RunPython(migrations.RunPython.noop, assert_no_duplicates_including_deleted),
    ]
