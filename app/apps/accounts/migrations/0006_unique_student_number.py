from django.db import migrations, models
from django.db.models import Count


def refuse_if_duplicates_exist(apps, schema_editor):
    """Fail early with an actionable message instead of a bare database error halfway through the deploy."""

    StudentProfile = apps.get_model("accounts", "StudentProfile")
    duplicates = (
        StudentProfile.objects.filter(is_deleted=False)
        .exclude(student_number="")
        .values("student_number")
        .annotate(total=Count("id"))
        .filter(total__gt=1)
    )
    duplicate_numbers = duplicates.count()
    if duplicate_numbers:
        affected = sum(row["total"] for row in duplicates)
        raise RuntimeError(
            f"Cannot enforce unique student numbers: {duplicate_numbers} student number(s) are shared by "
            f"{affected} live student profiles. Resolve them first (keep the number on the rightful account and "
            "clear or correct it on the others), for example in a Django shell: "
            "StudentProfile.objects.filter(is_deleted=False).exclude(student_number='')"
            ".values('student_number').annotate(n=Count('id')).filter(n__gt=1). Then run migrate again."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0005_user_session_version"),
        ("universities", "0001_initial"),
    ]

    operations = [
        # Reverse is a no-op: removing the constraint (below) is the whole undo.
        migrations.RunPython(refuse_if_duplicates_exist, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="studentprofile",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_deleted", False), models.Q(("student_number", ""), _negated=True)),
                fields=("student_number",),
                name="unique_active_student_number",
            ),
        ),
    ]
