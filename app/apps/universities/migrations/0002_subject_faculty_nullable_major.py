import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Step 1/3: add the (still nullable) faculty FK and make major optional. Additive, safe on live data."""

    dependencies = [("universities", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="subject",
            name="faculty",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="subjects",
                to="universities.faculty",
            ),
        ),
        migrations.AlterField(
            model_name="subject",
            name="major",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="subjects",
                to="universities.major",
            ),
        ),
    ]
