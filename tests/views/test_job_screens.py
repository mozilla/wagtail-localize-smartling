import pytest

from django.urls import reverse
from wagtail_localize.models import Translation, TranslationSource

from testapp.factories import InfoPageFactory
from tests.factories import JobFactory


pytestmark = pytest.mark.django_db


@pytest.fixture
def job_of_two_pages(root_page, locales):
    """A job as an order makes them: several pages, and no source of its own."""
    sources = [
        TranslationSource.get_or_create_from_instance(InfoPageFactory(parent=root_page, title=title))[0]
        for title in ("First ordered page", "Second ordered page")
    ]
    job = JobFactory(source_instance=sources[0].get_source_instance(), files=sources, synced=True)
    job.translation_source = None
    job.save(update_fields=["translation_source"])
    # The inspect screen links each translation's target, which only exists once
    # the translation has been saved.
    for translation in Translation.objects.filter(smartling_jobs=job):
        translation.save_target(publish=False)
    return job, sources


def test_the_job_listing_names_every_page_the_job_carries(client, superuser, job_of_two_pages):
    _, sources = job_of_two_pages
    client.force_login(superuser)

    content = client.get(reverse("wagtail_localize_smartling_jobs:index")).content.decode()

    for source in sources:
        assert str(source.get_source_instance()) in content


def test_the_job_inspect_screen_links_every_page_the_job_carries(client, superuser, job_of_two_pages):
    job, sources = job_of_two_pages
    client.force_login(superuser)

    content = client.get(reverse("wagtail_localize_smartling_jobs:inspect", args=[job.pk])).content.decode()

    for source in sources:
        assert source.get_source_instance_edit_url() in content
