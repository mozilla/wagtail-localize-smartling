from django.core.management import BaseCommand

from wagtail_localize_smartling.models import Job
from wagtail_localize_smartling.utils import compute_content_hash


class Command(BaseCommand):
    """
    Management command intended to be run once to populate missing `Job.content_hash`es
    """

    def handle(self, *args, **kwargs) -> None:
        jobs = []
        for job in Job.objects.filter(content_hash="").select_related(
            "translation_source"
        ):
            job.content_hash = compute_content_hash(job.translation_source.export_po())
            jobs.append(job)

        if jobs:
            Job.objects.bulk_update(jobs, ["content_hash"])
            self.stdout.write(
                self.style.SUCCESS(
                    f"Successfully populated {len(jobs)} Smartling job content hashes"
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    "Found no Smartling jobs to populate the content_hash field for."
                )
            )
