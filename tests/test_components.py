import pytest

from bs4 import BeautifulSoup
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from wagtail.models import Locale
from wagtail_localize.models import TranslationSource

from wagtail_localize_smartling.components import LandedTranslationsPanel, OpenOrdersPanel
from wagtail_localize_smartling.models import LandedTranslationTask

from testapp.factories import InfoPageFactory, UserFactory
from testapp.models import InfoPage
from tests.factories import JobFactory, OrderFactory, OrderItemFactory


pytestmark = pytest.mark.django_db


def _make_incomplete_tasks(count):
    locale, _ = Locale.objects.get_or_create(language_code="fr")
    content_type = ContentType.objects.get_for_model(InfoPage)
    for i in range(count):
        LandedTranslationTask.objects.create(
            content_type=content_type,
            object_id=i + 1,
            relevant_locale=locale,
            completed_on=None,
            cancelled_on=None,
        )


# The headings of the panel's three groups, each shown only when it has rows.
PANEL_OPEN_ORDERS = "Open orders"
PANEL_NEEDS_SENDING_AGAIN = "Needs sending again"
PANEL_STUCK_STILL_RETRYING = "Stuck, still retrying"
# What each group tells the reader. The heading alone says too little to act on,
# so a group that loses its explanation has lost half its point.
PANEL_HELP_TEXT = {
    PANEL_OPEN_ORDERS: (
        "These orders are still being assembled. Nothing in them reaches translators until someone sends them."
    ),
    PANEL_NEEDS_SENDING_AGAIN: (
        "The last send of these orders stopped before Smartling had everything, and nothing will retry it."
    ),
    PANEL_STUCK_STILL_RETRYING: (
        "The last sync of these orders did not finish. They are still being retried, so these may clear on their own."
    ),
}


def dashboard_panel(client, user):
    """The orders panel on the dashboard, or None when it is not shown."""
    client.force_login(user)
    content = client.get(reverse("wagtailadmin_home")).content.decode()
    return BeautifulSoup(content, "html.parser").select_one("#smartling-orders")


def panel_text(panel) -> str:
    """The panel's own text, apart from the rest of the dashboard."""
    return panel.get_text(separator=" ", strip=True) if panel else ""


def assert_groups_shown(panel, *headings):
    """The panel shows these groups, in this order, with their explanations, and no others."""
    assert panel is not None, "The panel is not on the dashboard at all"
    assert [group.get_text(strip=True) for group in panel.select("h3")] == list(headings)
    content = panel_text(panel)
    for heading, help_text in PANEL_HELP_TEXT.items():
        assert (help_text in content) is (heading in headings), f"Wrong explanation shown for {heading!r}"


def assert_panel_absent(panel):
    """Nothing needs a person, so the dashboard carries no panel."""
    assert panel is None, f"The panel is on the dashboard: {panel_text(panel)}"


def _add_page_to_order(order, root_page, user, title="Page in an order"):
    page = InfoPageFactory(parent=root_page, title=title)
    translation_source, _ = TranslationSource.get_or_create_from_instance(page)
    return OrderItemFactory(order=order, translation_source=translation_source, added_by=user)


@pytest.fixture()
def user_order_viewer():
    """Someone who may read the translation queue but not change an order."""
    user = UserFactory(is_superuser=False)
    user.user_permissions.add(Permission.objects.get(codename="access_admin"))
    user.user_permissions.add(
        Permission.objects.get(content_type__app_label="wagtail_localize_smartling", codename="view_order")
    )
    return user


def first_order_link(client, user) -> str:
    panel = dashboard_panel(client, user)
    assert panel is not None, "The panel is not on the dashboard at all"
    return panel.select_one("ul.listing li a")["href"]


def test_LandedTranslationsPanel_reports_total_not_sliced_count():
    # Regression test for #64: the panel heading and "See all" link must show
    # the full count of incomplete tasks, not the size of the truncated slice.
    total = 12
    max_to_show = 7
    _make_incomplete_tasks(total)

    panel = LandedTranslationsPanel(max_to_show=max_to_show)
    context = panel.get_context_data()

    assert context["total_tasks"] == total
    assert len(context["tasks"]) == max_to_show

    rendered = panel.render_html()

    assert f"{total} translations must be published" in rendered
    assert f"See all {total} tasks" in rendered
    # The truncated count must not appear as either the headline or the link.
    assert f"See all {max_to_show} tasks" not in rendered


def test_LandedTranslationsPanel_hides_see_all_when_not_truncated():
    max_to_show = 7
    _make_incomplete_tasks(3)

    panel = LandedTranslationsPanel(max_to_show=max_to_show)
    rendered = panel.render_html()

    assert "See all" not in rendered


def test_the_panel_lists_open_orders_with_their_age_and_size(client, root_page, superuser):
    order = OrderFactory(submitted_at=None, created_by=superuser, name="WNP 157")
    _add_page_to_order(order, root_page, superuser)

    panel = dashboard_panel(client, superuser)

    assert_groups_shown(panel, PANEL_OPEN_ORDERS)
    content = panel_text(panel)
    assert "WNP 157" in content
    assert "1 page" in content
    assert "1 pages" not in content
    assert "opened" in content.lower()


def test_the_panel_lists_an_order_whose_submission_failed(client, root_page, superuser):
    order = OrderFactory(created_by=superuser, name="Broken")
    job = JobFactory(source_instance=root_page, order=order)
    job.record_sync_failure(ValueError("duplicate name"), permanent=True, pushing=True)

    panel = dashboard_panel(client, superuser)

    assert_groups_shown(panel, PANEL_NEEDS_SENDING_AGAIN)
    assert "Broken" in panel_text(panel)


def test_the_panel_lists_an_order_whose_upload_stopped_part_way(client, root_page, superuser):
    order = OrderFactory(created_by=superuser, name="Half sent")
    job = JobFactory(source_instance=root_page, order=order, synced=True)
    job.record_sync_failure(ValueError("file rejected"), permanent=True, pushing=True)

    panel = dashboard_panel(client, superuser)

    assert_groups_shown(panel, PANEL_NEEDS_SENDING_AGAIN)
    assert "Half sent" in panel_text(panel)


def test_the_panel_lists_an_order_whose_send_is_stuck(client, root_page, superuser):
    order = OrderFactory(created_by=superuser, name="Stalled")
    job = JobFactory(source_instance=root_page, order=order, synced=True)
    job.record_sync_failure(TimeoutError("batch has not moved"), permanent=False, pushing=False)

    panel = dashboard_panel(client, superuser)

    assert_groups_shown(panel, PANEL_STUCK_STILL_RETRYING)
    assert "Stalled" in panel_text(panel)


def test_the_panel_is_absent_when_there_is_nothing_to_do(client, root_page, superuser):
    # An order being translated with nothing wrong with it should not be shown,
    # because it does not need any user action.
    order = OrderFactory(created_by=superuser, name="All done")
    JobFactory(source_instance=root_page, order=order, synced=True)

    assert_panel_absent(dashboard_panel(client, superuser))


def test_the_panel_is_hidden_from_a_user_without_view_order(client, root_page, regular_user, superuser):
    order = OrderFactory(submitted_at=None, created_by=superuser, name="WNP 157")
    _add_page_to_order(order, root_page, superuser)

    assert_panel_absent(dashboard_panel(client, regular_user))


def test_an_open_order_links_to_its_edit_screen_for_someone_who_can_change_it(client, root_page, superuser):
    order = OrderFactory(submitted_at=None, created_by=superuser, name="WNP 157")
    _add_page_to_order(order, root_page, superuser)

    assert first_order_link(client, superuser) == reverse("wagtail_localize_smartling_jobs:edit", args=(order.pk,))


def test_an_open_order_links_to_the_read_only_screen_for_someone_who_cannot_change_it(
    client, root_page, user_order_viewer, superuser
):
    order = OrderFactory(submitted_at=None, created_by=superuser, name="WNP 157")
    _add_page_to_order(order, root_page, superuser)

    assert first_order_link(client, user_order_viewer) == reverse(
        "wagtail_localize_smartling_jobs:inspect", args=(order.pk,)
    )


def test_the_panel_counts_every_open_order_not_only_the_ones_it_shows(root_page, superuser):
    for index in range(4):
        order = OrderFactory(submitted_at=None, created_by=superuser, name=f"Order {index}")
        _add_page_to_order(order, root_page, superuser, title=f"Page {index}")

    rendered = OpenOrdersPanel(user=superuser, max_to_show=2).render_html()

    assert "See all 4 open orders" in rendered
    # max_to_show=2 means that only the 2 oldest orders shown, so the two newest
    # orders are the ones left out.
    assert "Order 0" in rendered
    assert "Order 3" not in rendered


def test_the_panel_says_how_many_failed_sends_it_left_out(root_page, superuser):
    for index in range(4):
        order = OrderFactory(created_by=superuser, name=f"Broken {index}")
        job = JobFactory(source_instance=root_page, order=order)
        job.record_sync_failure(ValueError("nope"), permanent=True, pushing=True)

    rendered = OpenOrdersPanel(user=superuser, max_to_show=2).render_html()

    # There is no filter for 2 of the orders, so there is no specific URL to
    # link to for them.
    assert "and 2 more" in rendered
