from django.db import migrations


def backfill_faculty(apps, schema_editor):
    Subject = apps.get_model("universities", "Subject")
    Major = apps.get_model("universities", "Major")
    db = schema_editor.connection.alias
    for major in Major.objects.using(db).all().iterator():
        Subject.objects.using(db).filter(major_id=major.pk, faculty__isnull=True).update(faculty_id=major.faculty_id)
    # Code was unique per major, it becomes unique per faculty: disambiguate clashes between majors of one faculty.
    seen: set[tuple[int, str]] = set()
    for subject in Subject.objects.using(db).exclude(code="").order_by("id").iterator():
        key = (subject.faculty_id, subject.code)
        if key in seen:
            subject.code = f"{subject.code[:24]}-{subject.pk}"[:32]
            subject.save(update_fields=["code"])
        seen.add((subject.faculty_id, subject.code))


class Migration(migrations.Migration):
    """Step 2/3: every existing subject gets the faculty of its major. Reverse is a no-op (the column is dropped)."""

    dependencies = [("universities", "0002_subject_faculty_nullable_major")]

    operations = [migrations.RunPython(backfill_faculty, migrations.RunPython.noop)]
