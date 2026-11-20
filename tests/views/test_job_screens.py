import pytest

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from wagtail_localize.models import Translation, TranslationSource

from testapp.factories import InfoPageFactory
from tests.factories import JobFactory


pytestmark = pytest.mark.django_db


def build_job_of_two_pages(root_page, title_prefix):
    """A job as an order makes them: several pages, and no source of its own."""
    sources = [
        TranslationSource.get_or_create_from_instance(
            InfoPageFactory(parent=root_page, title=f"{title_prefix} page {page_number}")
        )[0]
        for page_number in (1, 2)
    ]
    job = JobFactory(source_instance=sources[0].get_source_instance(), files=sources, synced=True)
    job.translation_source = None
    job.save(update_fields=["translation_source"])
    return job, sources


def queries_against(captured, table):
    return len([query for query in captured.captured_queries if table in query["sql"]])


@pytest.fixture
def job_of_two_pages(root_page, locales):
    job, sources = build_job_of_two_pages(root_page, "Ordered")
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


def test_the_job_listing_same_db_queries_whatever_its_length(client, superuser, root_page, locales):
    client.force_login(superuser)
    url = reverse("wagtail_localize_smartling_jobs:index")

    def count_source_queries():
        # The first request warms caches that outlive it, such as the user's
        # permissions.
        client.get(url)
        with CaptureQueriesContext(connection) as captured:
            response = client.get(url)
        assert response.status_code == 200
        return {
            "job files": queries_against(captured, "smartling_jobfile"),
            "content types": queries_against(captured, "django_content_type"),
        }

    build_job_of_two_pages(root_page, "First")
    one_job = count_source_queries()

    for title_prefix in ("Second", "Third", "Fourth"):
        build_job_of_two_pages(root_page, title_prefix)
    four_jobs = count_source_queries()

    assert four_jobs == one_job


def test_the_job_inspect_screen_links_every_page_the_job_carries(client, superuser, job_of_two_pages):
    job, sources = job_of_two_pages
    client.force_login(superuser)

    content = client.get(reverse("wagtail_localize_smartling_jobs:inspect", args=[job.pk])).content.decode()

    for source in sources:
        assert source.get_source_instance_edit_url() in content


def test_the_inspect_screen_fetches_each_page_it_lists_once(client, superuser, job_of_two_pages):
    job, sources = job_of_two_pages
    client.force_login(superuser)
    url = reverse("wagtail_localize_smartling_jobs:inspect", args=[job.pk])
    # The first request warms caches that outlive it, such as the user's
    # permissions.
    client.get(url)

    with CaptureQueriesContext(connection) as captured:
        response = client.get(url)

    assert response.status_code == 200
    assert queries_against(captured, "testapp_infopage") == len(sources) + job.translations.count()
