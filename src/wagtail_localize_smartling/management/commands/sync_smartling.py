import logging

from django.core.management import BaseCommand

from wagtail_localize_smartling.constants import FINAL_STATUSES
from wagtail_localize_smartling.models import Job, Project
from wagtail_localize_smartling.sync import SyncJobException, sync_job


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Management command intended to be run on a schedule (e.g. every 10 minutes) that:
    - Picks up any pending translation jobs that need to be sent to Smartling
    - Checks the status of any unfinalised jobs and updates them as appropriate
    - Applies any new translations
    """

    def handle(self, *args, **kwargs) -> None:
        project = Project.get_current()

        for job_id in Job.objects.exclude(
            project=project, status__in=FINAL_STATUSES
        ).values_list("pk", flat=True):
            try:
                sync_job(job_id)
            except SyncJobException:
                logger.exception("Error syncing job with ID %s", job_id)
