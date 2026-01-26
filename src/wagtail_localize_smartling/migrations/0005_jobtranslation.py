# Generated manually for issues #28 and #37

import django.db.models.deletion

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("wagtail_localize", "0016_rename_page_revision_translationlog_revision"),
        ("wagtail_localize_smartling", "0004_landedtranslationtask"),
    ]

    operations = [
        migrations.CreateModel(
            name="JobTranslation",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("imported_at", models.DateTimeField(editable=False, null=True)),
                (
                    "job",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="job_translations",
                        to="wagtail_localize_smartling.job",
                    ),
                ),
                (
                    "translation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="smartling_job_translations",
                        to="wagtail_localize.translation",
                    ),
                ),
            ],
        ),
        migrations.AlterUniqueTogether(
            name="jobtranslation",
            unique_together={("job", "translation")},
        ),
    ]
