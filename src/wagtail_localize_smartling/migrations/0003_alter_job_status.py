# Generated by Django 5.0.6 on 2024-05-29 08:41

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("wagtail_localize_smartling", "0002_job_status_job_translation_job_uid"),
    ]

    operations = [
        migrations.AlterField(
            model_name="job",
            name="status",
            field=models.CharField(
                choices=[
                    ("UNSYNCED", "Unsynced"),
                    ("DRAFT", "Draft"),
                    ("AWAITING_AUTHORIZATION", "Awaiting authorization"),
                    ("IN_PROGRESS", "In progress"),
                    ("COMPLETED", "Completed"),
                    ("CANCELLED", "Cancelled"),
                    ("CLOSED", "Closed"),
                    ("DELETED", "Deleted"),
                ],
                default="UNSYNCED",
                editable=False,
                max_length=32,
            ),
        ),
    ]
