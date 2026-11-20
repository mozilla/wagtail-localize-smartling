import pytest

from bs4 import BeautifulSoup
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext
from wagtail_localize.models import TranslationSource

from wagtail_localize_smartling import models as wls_models
from wagtail_localize_smartling.api.types import JobStatus
from wagtail_localize_smartling.viewsets import OrderIndexView

from testapp.factories import InfoPageFactory
from tests.factories import JobFactory, OrderFactory


pytestmark = pytest.mark.django_db


def get_listing(client, superuser, **filters) -> str:
    client.force_login(superuser)
    return client.get(reverse("wagtail_localize_smartling_jobs:index"), filters).content.decode()


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


def listed_order_names(content: str) -> list[str]:
    """Every name the listing shows in its Order column."""
    listing = BeautifulSoup(content, "html.parser").select_one("table.listing")
    if listing is None:
        return []
    headings = [heading.get_text(separator=" ", strip=True) for heading in listing.select("thead th")]
    name_column = headings.index("Order")
    return [row.find_all("td")[name_column].get_text(strip=True) for row in listing.select("tbody tr")]


def add_pages_to(order, parent_page, user, *, page_count: int) -> None:
    for index in range(page_count):
        child = InfoPageFactory(parent=parent_page, title=f"{order.name} page {index}")
        source, _ = TranslationSource.get_or_create_from_instance(child)
        wls_models.OrderItem.objects.create(order=order, translation_source=source, added_by=user)


def land_translations(order, count: int | None = None) -> None:
    """Leave all translations of the order's latest job imported, as a landing does."""
    job_translations = list(order.get_latest_job().job_translations.order_by("pk"))
    for job_translation in job_translations[:count]:
        job_translation.imported_at = timezone.now()
        job_translation.save(update_fields=["imported_at"])


def open_one_order_per_state(superuser, root_page) -> list[wls_models.Order]:
    """One order in each state the Status column can show, each named after its own state."""
    states = wls_models.OrderState

    def open_order(state, **job_kwargs) -> wls_models.Order:
        order = OrderFactory(name=str(state.label), created_by=superuser)
        JobFactory(source_instance=root_page, order=order, **job_kwargs)
        return order

    partially_landed = open_order(states.PARTIALLY_LANDED, synced=True, status=JobStatus.IN_PROGRESS)
    land_translations(partially_landed, count=1)
    complete = open_order(states.COMPLETE, synced=True, status=JobStatus.COMPLETED)
    land_translations(complete)

    orders = [
        OrderFactory(name=str(states.NOT_SENT.label), submitted_at=None, created_by=superuser),
        open_order(states.FAILED, unsynced=True, sync_blocked=True),
        open_order(states.AWAITING_AUTHORIZATION, synced=True, status=JobStatus.AWAITING_AUTHORIZATION),
        open_order(states.IN_TRANSLATION, synced=True, status=JobStatus.IN_PROGRESS),
        partially_landed,
        complete,
        open_order(states.CANCELLED, synced=True, status=JobStatus.CANCELLED),
    ]
    assert {order.state for order in orders} == set(states), (
        "This helper function failed to create an order for each of the possible order states"
    )
    return orders


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


def test_needs_attention_finds_the_orders_whose_latest_job_went_wrong(client, superuser, root_page):
    open_one_order_per_state(superuser, root_page)

    content = get_listing(client, superuser, needs_attention="true")

    assert sorted(listed_order_names(content)) == sorted(
        [wls_models.OrderState.FAILED.label, wls_models.OrderState.CANCELLED.label]
    )


def test_no_order_falls_between_the_two_attention_groups(client, superuser, root_page):
    """The "No" answer is the whole complement, not everything the database felt sure about."""
    orders = open_one_order_per_state(superuser, root_page)

    needing_attention = listed_order_names(get_listing(client, superuser, needs_attention="true"))
    the_rest = listed_order_names(get_listing(client, superuser, needs_attention="false"))

    assert set(needing_attention) | set(the_rest) == {order.name for order in orders}


def test_a_resent_order_is_not_held_against_the_job_it_recovered_from(client, superuser, root_page):
    order = OrderFactory(name="Resent", created_by=superuser)
    JobFactory(source_instance=root_page, order=order, synced=True, status=JobStatus.CANCELLED)
    JobFactory(source_instance=root_page, order=order, synced=True, status=JobStatus.IN_PROGRESS)
    never_resent = OrderFactory(name="Still cancelled", created_by=superuser)
    JobFactory(source_instance=root_page, order=never_resent, synced=True, status=JobStatus.CANCELLED)

    content = get_listing(client, superuser, needs_attention="false")

    # The filter and the Status column read the same annotation, so an order the
    # listing calls "In translation" is one the filter leaves alone.
    assert listed_order_names(content) == ["Resent"]
    assert wls_models.Order.objects.get(pk=order.pk).state == wls_models.OrderState.IN_TRANSLATION


def test_filtering_by_status_shows_only_the_orders_in_that_state(client, superuser, root_page):
    open_one_order_per_state(superuser, root_page)

    # Get the Orders shown on the listing page for each of the Order states.
    orders_names_on_listing_page_by_state = {
        state: listed_order_names(get_listing(client, superuser, state=state.value)) for state in wls_models.OrderState
    }

    # Each Order state should have an Order on the listing page.
    assert orders_names_on_listing_page_by_state == {state: [str(state.label)] for state in wls_models.OrderState}


def test_the_order_column_does_not_borrow_a_sort_order_translation():
    """
    The "Order" model name is (so far) untranslated in this repository.

    This test proves that "order" does not accidentally get translated into the
    wrong meaning. Django's translation for "order" refers to an object's sort
    position. This app has an Order model which means something different, so it
    must not translate as 'sort position' in other languages.
    The proper thing to do would be to support locale translations in this
    repository, but that is its own task, so for now, we merely assert that
    "order" doesn't use Django's translation.
    """
    with translation.override("ru"):
        label = str(OrderIndexView().columns[0].label)

        assert label == "Order"
        assert gettext("Order") != label
