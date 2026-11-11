import html

import pytest

from bs4 import BeautifulSoup
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from wagtail.models import Locale
from wagtail_localize.models import Translation, TranslationSource

from wagtail_localize_smartling import models as wls_models

from testapp.factories import InfoPageFactory
from tests.factories import OrderFactory


pytestmark = pytest.mark.django_db


def edit_url(order) -> str:
    return reverse("wagtail_localize_smartling_jobs:edit", args=(order.pk,))


def inspect_url(order) -> str:
    return reverse("wagtail_localize_smartling_jobs:inspect", args=(order.pk,))


def text_of(content: str, selector: str) -> str:
    """One element's text as the screen reads it, with the markup's line breaks gone."""
    element = BeautifulSoup(content, "html.parser").select_one(selector)
    assert element is not None, f"No {selector} on the page"
    return " ".join(element.get_text().split())


def reported_messages(response) -> str:
    """
    The messages as the screen reads them.

    Wagtail renders each one through a template, so what is stored is escaped
    HTML padded with newlines: an apostrophe in the copy arrives as &#x27;.
    """
    return " ".join(" ".join(html.unescape(str(message)).split()) for message in response.context["messages"])


def ordered_translations(source):
    return [
        Translation.objects.get_or_create(source=source, target_locale=locale)[0]
        for locale in Locale.objects.exclude(pk=source.locale_id)
    ]


def draft_order_with_one_page(page, user) -> wls_models.Order:
    source, _ = TranslationSource.get_or_create_from_instance(page)
    order = OrderFactory(submitted_at=None, created_by=user)
    order_item = wls_models.OrderItem.objects.create(order=order, translation_source=source, added_by=user)
    order_item.translations.set(ordered_translations(source))
    return order


def submitted_order_with_one_page(page, user) -> wls_models.Order:
    order = draft_order_with_one_page(page, user)
    order.submitted_at = timezone.now()
    order.save(update_fields=["submitted_at"])
    return order


def test_the_draft_screen_shows_the_badge_the_fields_and_the_table(client, superuser, root_page):
    page = InfoPageFactory(parent=root_page, title="Firefox release notes")
    order = draft_order_with_one_page(page, superuser)
    client.force_login(superuser)

    content = client.get(edit_url(order)).content.decode()

    assert "Not sent to Smartling" in content
    assert "Becomes the Smartling job name when the order is submitted." in content
    assert "Sent to Smartling as the job deadline, unless it has already passed." in content
    assert "Submit to Smartling" in content
    assert page.title in content
    assert text_of(content, ".order-coverage__count") == "1 page \u00b7 2 page-locale pairs"
    assert (
        "Check the gaps before you submit \u2014 a gap means this order will not translate "
        "that page into that locale." in content
    )
    assert f"Add to an open order \u2192 {order.name}." in content
    assert "This order is still being assembled." in content
    assert "Submitting creates one Smartling job containing that page." in content
    assert text_of(content, ".order-coverage__legend") == "will be sent"
    remove_button = BeautifulSoup(content, "html.parser").select_one("button[name=action-remove-item]")
    assert remove_button is not None, "No remove control on a draft's pages table"
    assert remove_button.get_text(strip=True) == "Remove"


def test_a_locale_nobody_ticked_still_gets_a_column_with_a_gap_in_it(client, superuser, root_page):
    """A gap is the only warning that a page was left out of a locale."""
    page = InfoPageFactory(parent=root_page, title="Firefox release notes")
    source, _ = TranslationSource.get_or_create_from_instance(page)
    order = OrderFactory(submitted_at=None, created_by=superuser)
    order_item = wls_models.OrderItem.objects.create(order=order, translation_source=source, added_by=superuser)
    order_item.translations.set(
        Translation.objects.get_or_create(source=source, target_locale=Locale.objects.get(language_code="fr"))[:1]
    )
    client.force_login(superuser)

    content = client.get(edit_url(order)).content.decode()

    assert "French: will be sent" in content
    assert "German: not requested" in content


def test_save_is_the_first_submit_button_the_editor_form_owns(client, superuser, root_page):
    """
    Where a browser submits a form implicitly, it does so through the first
    submit button the form owns. We explicitly make Save the first button.
    """
    page = InfoPageFactory(parent=root_page, title="Firefox release notes")
    order = draft_order_with_one_page(page, superuser)
    client.force_login(superuser)

    content = client.get(edit_url(order)).content.decode()

    editor_form = BeautifulSoup(content, "html.parser").select_one("form#w-editor-form")
    assert editor_form is not None, "No editor form on the page"
    # A button naming another form belongs to that one, wherever it sits.
    own_submits = [
        button
        for button in editor_form.select("button[type=submit]")
        if button.get("form") in (None, editor_form.get("id"))
    ]
    assert own_submits, "The editor form has no submit button of its own"
    assert own_submits[0].get("name") is None, "Save is not the editor form's first submit button"


def test_removing_a_page_does_not_carry_the_unsaved_fields_with_it(client, superuser, root_page):
    """Remove posts on its own, so Wagtail's unsaved-changes guard still runs."""
    page = InfoPageFactory(parent=root_page, title="Firefox release notes")
    order = draft_order_with_one_page(page, superuser)
    client.force_login(superuser)

    content = client.get(edit_url(order)).content.decode()

    soup = BeautifulSoup(content, "html.parser")
    remove_button = soup.select_one("button[name=action-remove-item]")
    assert remove_button is not None, "No remove button in the pages table"
    removal_form = soup.select_one(f"form#{remove_button['form']}")
    assert removal_form is not None, "The remove button names a form that is not on the page"
    assert removal_form.select_one("input[name=name]") is None


def test_an_order_with_nothing_left_to_translate_is_not_reported_as_an_outage(client, superuser, root_page):
    page = InfoPageFactory(parent=root_page, title="Firefox release notes")
    source, _ = TranslationSource.get_or_create_from_instance(page)
    order = OrderFactory(submitted_at=None, created_by=superuser)
    wls_models.OrderItem.objects.create(order=order, translation_source=source, added_by=superuser)
    client.force_login(superuser)

    response = client.post(
        edit_url(order),
        {"name": order.name, "notes": "", "due_date": "", "action-submit-order": "1"},
        follow=True,
    )
    order.refresh_from_db()

    reported = reported_messages(response)
    assert order.is_draft
    assert "nothing left to translate" in reported
    assert "try again" not in reported


def test_an_empty_order_offers_nothing_to_submit(client, superuser, root_page):
    order = OrderFactory(submitted_at=None, created_by=superuser)
    client.force_login(superuser)

    content = client.get(edit_url(order)).content.decode()

    assert "Submit to Smartling" not in content
    assert "0 pages" not in content


def test_the_draft_screen_same_db_query_count_whatever_its_size(client, superuser, root_page):
    """Every value the table shows is resolved in bulk, not once per row."""

    def open_draft(page_count: int):
        order = OrderFactory(submitted_at=None, created_by=superuser)
        for index in range(page_count):
            child = InfoPageFactory(parent=root_page, title=f"{order.name} page {index}")
            source, _ = TranslationSource.get_or_create_from_instance(child)
            order_item = wls_models.OrderItem.objects.create(order=order, translation_source=source, added_by=superuser)
            order_item.translations.set(ordered_translations(source))
        return order

    two_pages = open_draft(2)
    eight_pages = open_draft(8)
    client.force_login(superuser)

    def count_queries_for(order) -> int:
        with CaptureQueriesContext(connection) as queries:
            client.get(edit_url(order))
        return len(queries)

    assert count_queries_for(eight_pages) == count_queries_for(two_pages)


def test_a_malformed_removal_is_refused_rather_than_crashing(client, superuser, root_page):
    page = InfoPageFactory(parent=root_page, title="Firefox release notes")
    order = draft_order_with_one_page(page, superuser)
    client.force_login(superuser)

    response = client.post(edit_url(order), {"action-remove-item": "not-a-pk"})

    assert response.status_code == 200
    assert order.order_items.count() == 1


def test_removing_a_page_that_is_not_on_this_order_removes_nothing_and_says_nothing(client, superuser, root_page):
    """Two tabs on one draft: the second click must not claim a removal that never happened."""
    order = draft_order_with_one_page(InfoPageFactory(parent=root_page, title="Firefox release notes"), superuser)
    other_order = draft_order_with_one_page(InfoPageFactory(parent=root_page, title="Another order"), superuser)
    other_item = other_order.order_items.get()
    client.force_login(superuser)

    response = client.post(edit_url(order), {"action-remove-item": str(other_item.pk)}, follow=True)

    assert other_order.order_items.count() == 1
    assert "does not delete the translation" not in reported_messages(response)


def test_a_page_left_out_of_the_send_is_not_called_already_translated(client, superuser, root_page, smartling_project):
    order = draft_order_with_one_page(InfoPageFactory(parent=root_page, title="Firefox release notes"), superuser)
    untouched = InfoPageFactory(parent=root_page, title="Nobody ticked a locale")
    source, _ = TranslationSource.get_or_create_from_instance(untouched)
    wls_models.OrderItem.objects.create(order=order, translation_source=source, added_by=superuser)
    client.force_login(superuser)

    response = client.post(
        edit_url(order),
        {"name": order.name, "notes": "", "due_date": "", "action-submit-order": "1"},
        follow=True,
    )

    reported = reported_messages(response)
    assert "already translated" not in reported
    assert untouched.title in reported


def test_saving_a_draft_does_not_send_it(client, superuser, root_page):
    page = InfoPageFactory(parent=root_page, title="Firefox release notes")
    order = draft_order_with_one_page(page, superuser)
    client.force_login(superuser)

    client.post(edit_url(order), {"name": "WNP 158", "notes": "", "due_date": ""})
    order.refresh_from_db()

    assert order.name == "WNP 158"
    assert order.submitted_at is None
    assert order.jobs.count() == 0


def test_submitting_creates_one_job_and_closes_the_order(client, superuser, root_page, smartling_project):
    page = InfoPageFactory(parent=root_page, title="Firefox release notes")
    order = draft_order_with_one_page(page, superuser)
    client.force_login(superuser)

    response = client.post(
        edit_url(order),
        {"name": order.name, "notes": "", "due_date": "", "action-submit-order": "1"},
    )
    order.refresh_from_db()

    assert order.submitted_at is not None
    assert order.jobs.count() == 1
    assert response.url == inspect_url(order)


def test_a_submission_that_fails_leaves_the_order_editable(client, superuser, root_page, mocker):
    page = InfoPageFactory(parent=root_page, title="Firefox release notes")
    order = draft_order_with_one_page(page, superuser)
    mocker.patch.object(wls_models.Order, "submit", side_effect=RuntimeError("Smartling is down"))
    client.force_login(superuser)

    response = client.post(
        edit_url(order),
        {"name": "WNP 158", "notes": "", "due_date": "", "action-submit-order": "1"},
    )
    order.refresh_from_db()

    assert order.is_draft
    assert order.name == "WNP 158"
    assert response.url == edit_url(order)


def test_removing_a_page_keeps_the_translation_it_already_created(client, superuser, root_page):
    page = InfoPageFactory(parent=root_page, title="Firefox release notes")
    order = draft_order_with_one_page(page, superuser)
    item = order.order_items.get()
    translation_count = Translation.objects.filter(source=item.translation_source).count()
    client.force_login(superuser)

    # Only what the removal form carries: it holds none of the editor's fields.
    client.post(edit_url(order), {"action-remove-item": str(item.pk)})

    assert order.order_items.count() == 0
    assert Translation.objects.filter(source=item.translation_source).count() == translation_count


def test_opening_a_sent_order_in_the_edit_view_redirects_to_inspect(client, superuser, root_page):
    page = InfoPageFactory(parent=root_page, title="Firefox release notes")
    order = submitted_order_with_one_page(page, superuser)
    client.force_login(superuser)

    response = client.get(edit_url(order))

    assert response.status_code == 302
    assert response.url == inspect_url(order)
    assert f"'{order}' was sent to Smartling and can no longer be edited" in reported_messages(
        client.get(inspect_url(order))
    )


def test_a_post_to_a_sent_order_is_redirected_rather_than_re_rendered(client, superuser, root_page):
    page = InfoPageFactory(parent=root_page, title="Firefox release notes")
    order = submitted_order_with_one_page(page, superuser)
    original_name = order.name
    client.force_login(superuser)

    response = client.post(edit_url(order), {"name": "Renamed after sending", "notes": "", "due_date": ""})
    order.refresh_from_db()

    assert response.status_code == 302
    assert response.url == inspect_url(order)
    assert order.name == original_name


def test_a_stale_tab_cannot_remove_a_page_from_a_sent_order(client, superuser, root_page):
    page = InfoPageFactory(parent=root_page, title="Firefox release notes")
    order = submitted_order_with_one_page(page, superuser)
    order_item = order.order_items.get()
    client.force_login(superuser)

    response = client.post(edit_url(order), {"action-remove-item": str(order_item.pk)})

    assert response.status_code == 302
    assert response.url == inspect_url(order)
    assert wls_models.OrderItem.objects.filter(pk=order_item.pk).exists()


def test_a_user_without_change_permission_cannot_open_the_draft_screen(client, regular_user, root_page):
    page = InfoPageFactory(parent=root_page, title="Firefox release notes")
    order = draft_order_with_one_page(page, regular_user)
    client.force_login(regular_user)

    response = client.get(edit_url(order))

    assert response.status_code == 302
    assert response.url == reverse("wagtailadmin_home")


def test_an_ordinary_editor_can_open_submit_and_read_an_order(client, editor_user, root_page, smartling_project):
    """Someone who is not a superuser can reach the listing, the draft and the record."""
    page = InfoPageFactory(parent=root_page, title="Firefox release notes")
    order = draft_order_with_one_page(page, editor_user)
    client.force_login(editor_user)

    assert client.get(reverse("wagtail_localize_smartling_jobs:index")).status_code == 200
    assert client.get(edit_url(order)).status_code == 200

    response = client.post(
        edit_url(order),
        {"name": order.name, "notes": "", "due_date": "", "action-submit-order": "1"},
    )
    order.refresh_from_db()

    assert not order.is_draft
    assert order.notes == ""
    assert response.url == inspect_url(order)
    assert client.get(inspect_url(order)).status_code == 200
