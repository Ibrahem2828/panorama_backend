import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Step 3/3: faculty becomes NOT NULL and uniqueness moves from per-major to per-faculty."""

    dependencies = [("universities", "0003_backfill_subject_faculty")]

    operations = [
        migrations.RemoveConstraint(model_name="subject", name="unique_subject_name_per_major_year_semester"),
        migrations.RemoveConstraint(model_name="subject", name="unique_subject_code_per_major"),
        migrations.RemoveIndex(model_name="subject", name="universitie_major_i_acf51f_idx"),
        migrations.AlterField(
            model_name="subject",
            name="faculty",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="subjects",
                to="universities.faculty",
            ),
        ),
        migrations.AlterModelOptions(
            name="subject",
            options={"ordering": ["faculty__name", "academic_year__order", "semester__order", "name"]},
        ),
        migrations.AddIndex(
            model_name="subject",
            index=models.Index(
                fields=["faculty", "academic_year", "semester", "is_active"], name="universitie_faculty_bd4467_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="subject",
            index=models.Index(fields=["faculty", "major", "is_active"], name="universitie_faculty_e07d4f_idx"),
        ),
        migrations.AddConstraint(
            model_name="subject",
            constraint=models.UniqueConstraint(
                condition=models.Q(("major__isnull", False)),
                fields=("faculty", "major", "academic_year", "semester", "name"),
                name="unique_subject_name_per_major_year_semester",
            ),
        ),
        migrations.AddConstraint(
            model_name="subject",
            constraint=models.UniqueConstraint(
                condition=models.Q(("major__isnull", True)),
                fields=("faculty", "academic_year", "semester", "name"),
                name="unique_subject_name_per_faculty_year_semester_no_major",
            ),
        ),
        migrations.AddConstraint(
            model_name="subject",
            constraint=models.UniqueConstraint(
                condition=models.Q(("code", ""), _negated=True),
                fields=("faculty", "code"),
                name="unique_subject_code_per_faculty",
            ),
        ),
    ]
