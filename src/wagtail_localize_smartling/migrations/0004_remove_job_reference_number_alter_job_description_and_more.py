# Generated by Django 5.0.6 on 2024-05-29 14:54

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("wagtail_localize_smartling", "0003_alter_job_status"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="job",
            name="reference_number",
        ),
        migrations.AlterField(
            model_name="job",
            name="description",
            field=models.TextField(blank=True, editable=False),
        ),
        migrations.AlterField(
            model_name="job",
            name="name",
            field=models.CharField(editable=False, max_length=170),
        ),
    ]
