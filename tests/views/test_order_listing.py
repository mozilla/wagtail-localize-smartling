import pytest

from bs4 import BeautifulSoup
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from wagtail_localize.models import TranslationSource

from wagtail_localize_smartling import models as wls_models
from wagtail_localize_smartling.api.types import JobStatus

from testapp.factories import InfoPageFactory
from tests.factories import JobFactory, OrderFactory


pytestmark = pytest.mark.django_db


def get_listing(client, superuser) -> str:
    client.force_login(superuser)
    return client.get(reverse("wagtail_localize_smartling_jobs:index")).content.decode()


RELATIVE_DATE_COLUMNS = ("Opened", "Due date")


def listing_row_for(content: str, order_name: str) -> dict[str, str]:
    """
    The listing row for one order, keyed by column heading, as the screen reads it.

    The date columns are left out, because they render against the moment the
    test ran rather than against a value a test can state.
    """
    listing = BeautifulSoup(content, "html.parser").select_one("table.listing")
    assert listing is not None, "No listing table on the page"
    headings = [heading.get_text(separator=" ", strip=True) for heading in listing.select("thead th")]
    for row in listing.select("tbody tr"):
        cells = [cell.get_text(separator=" ", strip=True) for cell in row.find_all("td")]
        if any(order_name == cell for cell in cells):
            return {
                heading: cell
                for heading, cell in zip(headings, cells, strict=True)
                if heading not in RELATIVE_DATE_COLUMNS
            }
    raise AssertionError(f"No row for {order_name!r} in the listing")


def add_pages_to(order, parent_page, user, *, page_count: int) -> None:
    for index in range(page_count):
        child = InfoPageFactory(parent=parent_page, title=f"{order.name} page {index}")
        source, _ = TranslationSource.get_or_create_from_instance(child)
        wls_models.OrderItem.objects.create(order=order, translation_source=source, added_by=user)


def test_the_listing_shows_a_draft_a_sent_and_a_resent_order(client, superuser, root_page):
    OrderFactory(submitted_at=None, name="Draft order", created_by=superuser)
    sent = OrderFactory(name="Sent order", created_by=superuser)
    JobFactory(source_instance=root_page, order=sent, synced=True, status=JobStatus.IN_PROGRESS)
    resent = OrderFactory(name="Resent order", created_by=superuser)
    JobFactory(source_instance=root_page, order=resent, synced=True, status=JobStatus.CANCELLED)
    JobFactory(source_instance=root_page, order=resent, synced=True, status=JobStatus.IN_PROGRESS)

    content = get_listing(client, superuser)

    assert listing_row_for(content, "Draft order")["Status"] == "Not sent to Smartling"
    assert listing_row_for(content, "Sent order")["Status"] == "In translation"
    # The resend decides the row, so the cancelled job it recovered from is not
    # what the listing reports.
    assert listing_row_for(content, "Resent order")["Status"] == "In translation"


def test_the_listing_links_a_draft_to_edit_and_a_sent_order_to_inspect(client, superuser, root_page):
    draft = OrderFactory(submitted_at=None, created_by=superuser)
    sent = OrderFactory(created_by=superuser)
    JobFactory(source_instance=root_page, order=sent, synced=True)

    content = get_listing(client, superuser)

    assert reverse("wagtail_localize_smartling_jobs:edit", args=(draft.pk,)) in content
    assert reverse("wagtail_localize_smartling_jobs:inspect", args=(sent.pk,)) in content
    assert reverse("wagtail_localize_smartling_jobs:edit", args=(sent.pk,)) not in content


def test_the_listing_counts_the_pages_in_each_order(client, superuser, root_page):
    one_page = OrderFactory(submitted_at=None, name="One page", created_by=superuser)
    add_pages_to(one_page, root_page, superuser, page_count=1)
    three_pages = OrderFactory(submitted_at=None, name="Three pages", created_by=superuser)
    add_pages_to(three_pages, root_page, superuser, page_count=3)

    content = get_listing(client, superuser)

    assert listing_row_for(content, "One page") == {
        "Order": "One page",
        "Pages": "1",
        "Status": "Not sent to Smartling",
        "Opened by": superuser.get_username(),
    }
    assert listing_row_for(content, "Three pages") == {
        "Order": "Three pages",
        "Pages": "3",
        "Status": "Not sent to Smartling",
        "Opened by": superuser.get_username(),
    }


def test_the_listing_same_db_query_count_whatever_its_length(client, superuser, root_page):
    """Every value a row shows comes from the annotations, not from the row."""

    def open_orders(count: int) -> None:
        for _ in range(count):
            order = OrderFactory(created_by=superuser)
            JobFactory(source_instance=root_page, order=order, synced=True, status=JobStatus.IN_PROGRESS)

    def count_queries_for_the_listing() -> int:
        client.force_login(superuser)
        with CaptureQueriesContext(connection) as queries:
            client.get(reverse("wagtail_localize_smartling_jobs:index"))
        return len(queries)

    open_orders(2)
    two_orders = count_queries_for_the_listing()
    open_orders(6)

    assert count_queries_for_the_listing() == two_orders
