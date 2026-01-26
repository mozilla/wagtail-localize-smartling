# Generated manually for issues #28 and #37

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("wagtail_localize_smartling", "0006_populate_jobtranslation"),
    ]

    operations = [
        # Remove the auto-generated M2M table
        migrations.RemoveField(
            model_name="job",
            name="translations",
        ),
        # Add the new M2M field with the through model
        migrations.AddField(
            model_name="job",
            name="translations",
            field=models.ManyToManyField(
                related_name="smartling_jobs",
                through="wagtail_localize_smartling.JobTranslation",
                to="wagtail_localize.translation",
            ),
        ),
    ]
