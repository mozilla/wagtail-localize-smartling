from typing import TYPE_CHECKING
from urllib.parse import urljoin

import pytest

from wagtail.admin.utils import get_admin_base_url

from testapp.settings import job_description_callback


if TYPE_CHECKING:
    from wagtail_localize_smartling.models import Job

pytestmark = pytest.mark.django_db


def test_default_job_description(smartling_job: "Job"):
    page = smartling_job.translation_source.get_source_instance()
    url = urljoin(
        get_admin_base_url() or "",
        smartling_job.translation_source.get_source_instance_edit_url(),
    )
    assert smartling_job.description == (
        f'Automatically-created Wagtail translation job for info page "{page}". '
        f"The source content can be edited here: {url}"
    )


def test_job_description_callback(smartling_job: "Job", smartling_settings):
    smartling_settings.JOB_DESCRIPTION_CALLBACK = job_description_callback
    description = smartling_job.get_description(
        smartling_job.translation_source, smartling_job.translations.all()
    )
    assert description == "1337"
