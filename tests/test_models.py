from typing import TYPE_CHECKING

import pytest

from freezegun import freeze_time
from wagtail.models import Locale
from wagtail_localize.models import Translation, TranslationSource

from wagtail_localize_smartling.models import Job

from testapp.factories import InfoPageFactory
from testapp.settings import job_description_callback


if TYPE_CHECKING:
    from wagtail_localize_smartling.models import Job

pytestmark = pytest.mark.django_db


def test_default_job_description(smartling_job: "Job"):
    page = smartling_job.translation_source.get_source_instance()
    assert smartling_job.description == f"CMS translation job for info page '{page}'."


def test_job_description_callback(smartling_job: "Job", smartling_settings):
    smartling_settings.JOB_DESCRIPTION_CALLBACK = job_description_callback
    description = smartling_job.get_description(
        smartling_job.translation_source, smartling_job.translations.all()
    )
    assert description == "1337"


@pytest.mark.parametrize("name_prefix", (None, "Test prefix 123"))
@freeze_time("2024-05-03 12:34:56.123456")
def test_Job_get_default_name(name_prefix, smartling_settings, root_page):
    page = InfoPageFactory(parent=root_page, title="Component test page")
    translation_source, created = TranslationSource.get_or_create_from_instance(page)
    page_translation = Translation.objects.create(
        source=translation_source,
        target_locale=Locale.objects.get(language_code="fr"),
    )

    smartling_settings.JOB_NAME_PREFIX = name_prefix

    name = Job.get_default_name(translation_source, [page_translation])

    expected_name = f"70018f7c #{translation_source.pk}"

    if name_prefix:
        expected_name = f"{name_prefix} {expected_name}"

    assert expected_name == name
