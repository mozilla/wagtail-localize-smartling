import pytest

from bs4 import BeautifulSoup
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from wagtail.models import Locale
from wagtail_localize.models import Translation, TranslationSource

from wagtail_localize_smartling import models as wls_models
from wagtail_localize_smartling.api.types import JobStatus

from testapp.factories import InfoPageFactory
from tests.factories import JobFactory, OrderFactory


pytestmark = pytest.mark.django_db


def inspect_url(order) -> str:
    return reverse("wagtail_localize_smartling_jobs:inspect", args=(order.pk,))


def edit_url(order) -> str:
    return reverse("wagtail_localize_smartling_jobs:edit", args=(order.pk,))


def delete_url(order) -> str:
    return reverse("wagtail_localize_smartling_jobs:delete", args=(order.pk,))


def resubmit_url(job) -> str:
    return reverse("wagtail_localize_smartling_retry_job", args=(job.pk,))


def text_of(content: str, selector: str) -> str:
    """One element's text as the screen reads it, with the markup's line breaks gone."""
    element = BeautifulSoup(content, "html.parser").select_one(selector)
    assert element is not None, f"No {selector} on the page"
    return " ".join(element.get_text().split())


def ordered_translations(source):
    return [
        Translation.objects.get_or_create(source=source, target_locale=locale)[0]
        for locale in Locale.objects.exclude(pk=source.locale_id)
    ]


def add_pages_to(order, pages, user) -> list:
    sources = []
    for page in pages:
        source, _ = TranslationSource.get_or_create_from_instance(page)
        order_item = wls_models.OrderItem.objects.create(order=order, translation_source=source, added_by=user)
        order_item.translations.set(ordered_translations(source))
        sources.append(source)
    return sources


def submitted_order_with_pages(pages, user, notes="", **job_kwargs):
    order = OrderFactory(created_by=user, notes=notes)
    sources = add_pages_to(order, pages, user)
    # setdefault, so a caller can ask for the send that never reached Smartling.
    job_kwargs.setdefault("synced", True)
    job = JobFactory(source_instance=pages[0], order=order, user=user, files=sources, **job_kwargs)
    return order, job


def submitted_order_with_one_page(page, user, **job_kwargs):
    return submitted_order_with_pages([page], user, **job_kwargs)


def draft_order_with_one_page(page, user):
    order = OrderFactory(submitted_at=None, created_by=user)
    add_pages_to(order, [page], user)
    return order


def test_the_submitted_screen_is_read_only(client, superuser, root_page):
    page = InfoPageFactory(parent=root_page, title="Firefox release notes")
    order, _ = submitted_order_with_one_page(page, superuser)
    client.force_login(superuser)

    content = client.get(inspect_url(order)).content.decode()

    assert "This order has been sent and can no longer be edited." in content
    assert "Submit to Smartling" not in content
    assert "Remove" not in content
    assert edit_url(order) not in content
    assert delete_url(order) not in content


def test_the_screen_shows_the_badge_the_fields_and_the_notes(client, superuser, root_page):
    page = InfoPageFactory(parent=root_page, title="Firefox release notes")
    order, _ = submitted_order_with_one_page(page, superuser, notes="Keep the product names in English.")
    client.force_login(superuser)

    content = client.get(inspect_url(order)).content.decode()

    # Scoped: the send history's own status chip can carry the same words, and
    # the header and breadcrumbs carry the name whether the field list renders
    # or not.
    assert text_of(content, ".order-screen__intro .w-status") == str(order.state_label)
    assert text_of(content, "dl dd") == order.name
    assert "Keep the product names in English." in content
    assert f"Sent by {superuser}" in content


def test_the_screen_links_every_page_the_order_carries(client, superuser, root_page):
    pages = [InfoPageFactory(parent=root_page, title=f"Release notes {number}") for number in range(3)]
    order, _ = submitted_order_with_pages(pages, superuser)
    client.force_login(superuser)

    soup = BeautifulSoup(client.get(inspect_url(order)).content.decode(), "html.parser")

    for page in pages:
        page_url = reverse("wagtailadmin_pages:edit", args=[page.pk])
        assert soup.select_one(f'a[href="{page_url}"]') is not None, f"{page.title} is not linked"


def test_the_send_history_lists_every_send_newest_first(client, superuser, root_page):
    page = InfoPageFactory(parent=root_page, title="Firefox release notes")
    order, _ = submitted_order_with_one_page(page, superuser, status=JobStatus.CANCELLED)
    JobFactory(source_instance=page, order=order, user=superuser, synced=True, status=JobStatus.IN_PROGRESS)
    client.force_login(superuser)

    content = client.get(inspect_url(order)).content.decode()

    assert content.index("Send 2") < content.index("Send 1")
    assert content.count("View job in Smartling") == 2


def test_the_latest_send_does_not_offer_resubmit_if_nothing_can_arrive(client, superuser, root_page):
    page = InfoPageFactory(parent=root_page, title="Firefox release notes")
    order, cancelled = submitted_order_with_one_page(page, superuser, status=JobStatus.CANCELLED)
    client.force_login(superuser)

    assert resubmit_url(cancelled) in client.get(inspect_url(order)).content.decode()

    live = JobFactory(source_instance=page, order=order, user=superuser, synced=True, status=JobStatus.IN_PROGRESS)
    content = client.get(inspect_url(order)).content.decode()

    assert resubmit_url(cancelled) not in content
    assert resubmit_url(live) not in content


def test_a_send_blocked_before_smartling_saw_it_is_not_reported_as_unsynced(client, superuser, root_page):
    page = InfoPageFactory(parent=root_page, title="Firefox release notes")
    order, job = submitted_order_with_one_page(page, superuser, synced=False, unsynced=True, status=JobStatus.UNSYNCED)
    job.record_sync_failure(ValueError("duplicate job name"), permanent=True, pushing=True)
    client.force_login(superuser)

    content = client.get(inspect_url(order)).content.decode()

    assert "Submission failed" in content
    assert "duplicate job name" in content
    assert "View job in Smartling" not in content
    assert resubmit_url(job) not in content


def test_the_landed_count_is_shown(client, superuser, root_page):
    page = InfoPageFactory(parent=root_page, title="Firefox release notes")
    order, job = submitted_order_with_one_page(page, superuser)
    landed = job.job_translations.first()
    landed.imported_at = timezone.now()
    landed.save(update_fields=["imported_at"])
    client.force_login(superuser)

    content = client.get(inspect_url(order)).content.decode()

    assert f"1 of {job.job_translations.count()} landed" in content


def test_a_draft_opened_at_the_inspect_url_is_not_called_sent(client, superuser, root_page):
    page = InfoPageFactory(parent=root_page, title="Firefox release notes")
    order = draft_order_with_one_page(page, superuser)
    client.force_login(superuser)

    content = client.get(inspect_url(order)).content.decode()

    assert "This order has been sent and can no longer be edited." not in content
    assert "has not been sent to Smartling yet" in content


def test_the_submitted_screen_same_db_query_count_whatever_its_size(client, superuser, root_page):
    client.force_login(superuser)

    def query_count_for(page_count: int, send_count: int) -> int:
        pages = [
            InfoPageFactory(parent=root_page, title=f"Page {page_count}-{send_count}-{number}")
            for number in range(page_count)
        ]
        order, job = submitted_order_with_pages(pages, superuser)
        for _ in range(send_count - 1):
            JobFactory(
                source_instance=pages[0],
                order=order,
                user=superuser,
                files=[job_file.translation_source for job_file in job.files.select_related("translation_source")],
                synced=True,
            )
        with CaptureQueriesContext(connection) as queries:
            client.get(inspect_url(order))
        return len(queries)

    assert query_count_for(page_count=2, send_count=1) == query_count_for(page_count=8, send_count=3)


def test_an_order_with_no_due_date_does_not_report_one_called_none(client, superuser, root_page):
    page = InfoPageFactory(parent=root_page, title="Firefox release notes")
    order, _ = submitted_order_with_one_page(page, superuser)
    assert order.due_date is None
    client.force_login(superuser)

    content = client.get(inspect_url(order)).content.decode()

    values = [" ".join(value.get_text().split()) for value in BeautifulSoup(content, "html.parser").select("dl dd")]
    assert "None" not in values, "An empty field reads as the string None"
    assert "—" in values


def test_a_send_counts_the_locales_it_covers_not_its_page_locale_pairs(client, superuser, root_page):
    pages = [InfoPageFactory(parent=root_page, title=f"Release notes {number}") for number in range(2)]
    order, job = submitted_order_with_pages(pages, superuser)
    assert job.job_translations.count() == 4, "Two pages in two locales is the shape this test needs"
    client.force_login(superuser)

    content = client.get(inspect_url(order)).content.decode()

    assert "2 files" in content
    assert "2 locales" in content
    assert "4 locales" not in content


def test_a_sent_order_whose_jobs_are_gone_is_not_called_unsent(client, superuser, root_page):
    page = InfoPageFactory(parent=root_page, title="Firefox release notes")
    order, job = submitted_order_with_one_page(page, superuser)
    job.delete()
    client.force_login(superuser)

    content = client.get(inspect_url(order)).content.decode()

    assert "This order has not been sent to Smartling, so there is nothing to show here yet." not in content
    assert "no record of its Smartling jobs remains" in content


def test_a_draft_does_not_report_a_landed_count(client, superuser, root_page):
    page = InfoPageFactory(parent=root_page, title="Firefox release notes")
    order = draft_order_with_one_page(page, superuser)
    client.force_login(superuser)

    content = client.get(inspect_url(order)).content.decode()

    assert "landed" not in text_of(content, ".order-coverage__count")
