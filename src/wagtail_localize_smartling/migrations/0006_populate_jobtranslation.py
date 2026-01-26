# Generated manually for issues #28 and #37

from django.db import migrations


def forwards_func(apps, schema_editor):
    """
    Copy existing M2M relationships to the new JobTranslation through model.

    For existing jobs that have already imported translations (translations_imported_at
    is set), we set the imported_at field on all JobTranslation records to match.
    """
    Job = apps.get_model("wagtail_localize_smartling", "Job")
    JobTranslation = apps.get_model("wagtail_localize_smartling", "JobTranslation")

    for job in Job.objects.all():
        # Get translations from the auto-generated M2M table
        # The table is named wagtail_localize_smartling_job_translations
        translations = job.translations.all()

        for translation in translations:
            JobTranslation.objects.create(
                job=job,
                translation=translation,
                # If the job has already imported translations, mark all as imported
                imported_at=job.translations_imported_at,
            )


def backwards_func(apps, schema_editor):
    """
    The old M2M relationship should still exist, so we just clear the JobTranslation table.
    """
    JobTranslation = apps.get_model("wagtail_localize_smartling", "JobTranslation")
    JobTranslation.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("wagtail_localize_smartling", "0005_jobtranslation"),
    ]

    operations = [
        migrations.RunPython(forwards_func, backwards_func),
    ]
