# Generated by Django 5.1.3 on 2024-12-16 16:45

import django.db.models.deletion

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        ("wagtail_localize_smartling", "0003_data_add_translation_approver_auth_group"),
    ]

    operations = [
        migrations.CreateModel(
            name="LandedTranslationTask",
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
                ("object_id", models.PositiveIntegerField()),
                ("created_on", models.DateTimeField(auto_now_add=True)),
                ("completed_on", models.DateTimeField(blank=True, null=True)),
                ("cancelled_on", models.DateTimeField(blank=True, null=True)),
                (
                    "content_type",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="wagtail_localize_smartling_tasks",
                        to="contenttypes.contenttype",
                        verbose_name="content type",
                    ),
                ),
                (
                    "relevant_locale",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="wagtailcore.locale",
                    ),
                ),
            ],
        ),
    ]
