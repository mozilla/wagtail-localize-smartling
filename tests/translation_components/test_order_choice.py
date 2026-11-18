"""
Tests for the choice between sending a page to Smartling now and collecting it
into an order, offered on wagtail-localize's submit-for-translation form.
"""

from datetime import timedelta
from html import unescape

import pytest

from bs4 import BeautifulSoup
from django.urls import reverse
from django.utils import timezone
from wagtail.models import Locale
from wagtail_localize.views import submit_translations

from wagtail_localize_smartling import models as wls_models

from testapp.factories import InfoPageFactory, TranslationSourceFactory
from tests.factories import JobFactory, OrderFactory, OrderItemFactory


pytestmark = pytest.mark.django_db

COMPONENT_PREFIX = f"component-{wls_models.Job._meta.db_table}"


@pytest.fixture()
def page(root_page):
    return InfoPageFactory(parent=root_page, title="Component test page")


def submit_url(page):
    return reverse("wagtail_localize:submit_page_translation", kwargs={"page_id": page.pk})


def page_text(response):
    """The re-rendered submit page, with escaped characters back as an editor sees them."""
    return unescape(response.content.decode())


def component_form(response):
    """The Smartling component's own form, as the submit page rendered it."""
    return response.context["components"].components[0][2]


def component_form_errors(response):
    """The Smartling component's own errors, from a submit page re-rendered after a refusal."""
    return component_form(response).errors


def orders_in_open_orders_dropdown(response):
    """The orders the submit page's dropdown offers as 'Open orders'."""
    soup = BeautifulSoup(response.content.decode(), "html.parser")
    options = soup.select(f'select[name="{COMPONENT_PREFIX}-order"] option')
    return [option.get_text(strip=True) for option in options]


def add_children(page, count):
    for index in range(count):
        InfoPageFactory(parent=page, title=f"Child {index}")


def add_page_to_order(order, page, user):
    OrderItemFactory(order=order, translation_source=TranslationSourceFactory(source_instance=page), added_by=user)


def hold_the_subtree_task(mocker):
    """
    Keep wagtail-localize's descendant task from running inline.

    The default test backend runs it inside the same request, which leaves no
    window for the order to change in between; a real worker runs it minutes
    later, and that is the timing worth testing.
    """
    held = []
    mocker.patch.object(
        submit_translations.background,
        "enqueue",
        side_effect=lambda func, args, kwargs: held.append(lambda: func(*args, **kwargs)),
    )
    return held


def submit_data(page, include_subtree=False, **component_fields):
    data = {
        "locales": list(Locale.objects.exclude(pk=page.locale_id).values_list("pk", flat=True)),
        f"{COMPONENT_PREFIX}-enabled": True,
        f"{COMPONENT_PREFIX}-due_date": "",
    }
    if include_subtree:
        # include_subtree belongs to wagtail-localize's own form, which posts
        # alongside the component's and carries no prefix.
        data["include_subtree"] = "on"
    data.update({f"{COMPONENT_PREFIX}-{name}": value for name, value in component_fields.items()})
    return data


def test_send_now_is_the_default_and_creates_a_job(client, page, superuser, smartling_project):
    client.force_login(superuser)
    assert wls_models.Job.objects.count() == 0
    assert wls_models.Order.objects.count() == 0

    client.post(submit_url(page), submit_data(page))

    assert wls_models.Job.objects.count() == 1
    assert wls_models.Order.objects.count() == 1
    job = wls_models.Job.objects.get()
    # Every send-now job gets an order of its own holding one page, so the
    # order screens have nothing to special-case
    assert job.order is not None
    assert not job.order.is_draft
    assert job.order.order_items.count() == 1
    assert wls_models.Order.objects.filter(submitted_at__isnull=True).count() == 0


def test_starting_a_new_order_creates_the_order_and_no_job(client, page, superuser, smartling_project):
    client.force_login(superuser)
    assert wls_models.Job.objects.count() == 0
    assert wls_models.Order.objects.count() == 0

    client.post(
        submit_url(page),
        submit_data(page, how_to_send_choice="new", new_order_name="WNP 157", new_order_notes="Ship with 157."),
    )

    order = wls_models.Order.objects.get()
    assert order.name == "WNP 157"
    assert order.notes == "Ship with 157."
    assert order.submitted_at is None
    assert order.order_items.count() == 1
    assert wls_models.Job.objects.count() == 0


def test_adding_to_an_open_order_records_the_contributor(client, page, superuser, regular_user, smartling_project):
    order = OrderFactory(submitted_at=None, created_by=regular_user, name="WNP 157")
    client.force_login(superuser)
    assert wls_models.Order.objects.count() == 1

    client.post(submit_url(page), submit_data(page, how_to_send_choice="existing", order=order.pk))

    assert wls_models.Order.objects.count() == 1  # Another Order is not created
    order.refresh_from_db()
    assert [item.translation_source.object_id for item in order.order_items.all()] == [page.translation_key]
    assert superuser in order.contributors.all()
    assert wls_models.Job.objects.count() == 0


def test_only_send_now_and_a_new_order_are_offered_when_no_order_is_open(client, page, superuser, regular_user):
    client.force_login(superuser)

    content = client.get(submit_url(page)).content.decode()

    assert "Send now as its own job" in content
    assert "Start a new order" in content
    assert "Add to an open order" not in content
    # An empty dropdown is a choice a first-time user cannot make.
    assert f'<select name="{COMPONENT_PREFIX}-order"' not in content

    # If an Order exists, then the order field is shown.
    OrderFactory(submitted_at=None, created_by=regular_user, name="WNP 157")
    content = client.get(submit_url(page)).content.decode()
    assert "Add to an open order" in content
    assert f'<select name="{COMPONENT_PREFIX}-order"' in content


def test_only_orders_still_open_are_offered(client, page, superuser):
    OrderFactory(name="Already sent", created_by=superuser)  # submitted by default
    OrderFactory(name="Still open", created_by=superuser, submitted_at=None)
    client.force_login(superuser)

    content = client.get(submit_url(page)).content.decode()

    assert "Still open" in content
    assert "Already sent" not in content


def test_the_form_opens_when_an_open_order_s_creator_is_gone(client, page, superuser):
    OrderFactory(submitted_at=None, created_by=None, name="WNP 157")
    client.force_login(superuser)

    response = client.get(submit_url(page))

    assert response.status_code == 200
    assert "Add to an open order" in response.content.decode()


def test_adding_to_an_open_order_does_not_change_its_due_date(client, page, superuser, smartling_project):
    due = timezone.now() + timedelta(days=7)
    order = OrderFactory(submitted_at=None, created_by=superuser, name="WNP 157", due_date=due)
    client.force_login(superuser)

    client.post(
        submit_url(page),
        submit_data(page, how_to_send_choice="existing", order=order.pk, due_date="2099-01-01"),
    )
    order.refresh_from_db()

    assert order.order_items.count() == 1
    assert order.due_date == due


def test_a_new_order_without_a_name_is_refused(client, page, superuser):
    client.force_login(superuser)

    response = client.post(submit_url(page), submit_data(page, how_to_send_choice="new", new_order_name=""))

    assert "Give the order a name." in response.content.decode()
    assert wls_models.Order.objects.count() == 0
    assert wls_models.Job.objects.count() == 0


def test_adding_to_an_order_without_choosing_one_is_refused(client, page, superuser):
    OrderFactory(submitted_at=None, created_by=superuser, name="WNP 157")
    client.force_login(superuser)

    response = client.post(submit_url(page), submit_data(page, how_to_send_choice="existing", order=""))

    assert "Choose an order, or start a new one." in response.content.decode()
    assert wls_models.OrderItem.objects.count() == 0
    assert wls_models.Job.objects.count() == 0


def test_naming_a_new_order_while_sending_now_is_refused(client, page, superuser, smartling_project):
    client.force_login(superuser)

    response = client.post(
        submit_url(page),
        submit_data(page, new_order_name="WNP 157", new_order_notes="Ship with 157."),
    )

    assert response.status_code == 200
    assert "The values of 'Order name', 'Notes for translators' are not valid" in page_text(response)
    assert "how_to_send_choice" in component_form_errors(response)
    assert wls_models.Order.objects.count() == 0
    assert wls_models.Job.objects.count() == 0


def test_picking_an_order_while_sending_now_is_refused(client, page, superuser):
    order = OrderFactory(submitted_at=None, created_by=superuser, name="WNP 157")
    client.force_login(superuser)

    response = client.post(submit_url(page), submit_data(page, order=order.pk))

    assert "The value of 'Open order' is not valid" in page_text(response)
    assert wls_models.OrderItem.objects.count() == 0
    assert wls_models.Job.objects.count() == 0


def test_picking_an_order_while_starting_a_new_one_is_refused(client, page, superuser):
    order = OrderFactory(submitted_at=None, created_by=superuser, name="WNP 157")
    client.force_login(superuser)

    response = client.post(
        submit_url(page),
        submit_data(page, how_to_send_choice="new", new_order_name="WNP 158", order=order.pk),
    )

    assert "The value of 'Open order' is not valid" in page_text(response)
    assert list(wls_models.Order.objects.values_list("name", flat=True)) == [order.name]
    assert wls_models.Job.objects.count() == 0


def test_a_name_an_open_order_already_has_is_refused(client, page, superuser):
    OrderFactory(submitted_at=None, created_by=superuser, name="WNP 157")
    client.force_login(superuser)
    assert wls_models.Order.objects.count() == 1
    assert wls_models.Job.objects.count() == 0

    response = client.post(submit_url(page), submit_data(page, how_to_send_choice="new", new_order_name="  wnp 157  "))

    # Neither case nor the padding an editor pastes in makes a name different.
    assert "An order called 'wnp 157' is already open." in page_text(response)
    assert "new_order_name" in component_form_errors(response)
    assert wls_models.Order.objects.count() == 1
    assert wls_models.Job.objects.count() == 0


def test_a_name_a_sent_order_s_job_took_is_refused(client, page, superuser, smartling_settings):
    smartling_settings.JOB_NAME_PREFIX = "www.firefox.com"
    sent_order = OrderFactory(name="WNP 157", created_by=superuser)
    JobFactory(source_instance=page, order=sent_order, name=sent_order.suggested_job_name())
    client.force_login(superuser)
    assert wls_models.Order.objects.count() == 1
    assert wls_models.Job.objects.count() == 1

    response = client.post(submit_url(page), submit_data(page, how_to_send_choice="new", new_order_name="WNP 157"))

    assert "already called 'www.firefox.com WNP 157'" in page_text(response)
    assert "new_order_name" in component_form_errors(response)
    assert wls_models.Order.objects.count() == 1
    assert wls_models.Order.objects.filter(submitted_at__isnull=True).count() == 0
    assert wls_models.Job.objects.count() == 1


def test_a_name_held_only_by_a_sent_order_is_free(client, root_page, page, superuser, smartling_project):
    client.force_login(superuser)
    assert wls_models.Order.objects.count() == 0
    assert wls_models.Job.objects.count() == 0

    # Sending a page now names its order after the page, while that order's job
    # carries a generated name, so nothing at the Smartling end holds the title.
    client.post(submit_url(page), submit_data(page))
    assert wls_models.Order.objects.get().name == page.title
    another_page = InfoPageFactory(parent=root_page, title="Another page")
    assert wls_models.Order.objects.count() == 1
    assert wls_models.Job.objects.count() == 1

    client.post(
        submit_url(another_page),
        submit_data(another_page, how_to_send_choice="new", new_order_name=page.title),
    )

    assert wls_models.Order.objects.filter(submitted_at__isnull=True, name=page.title).count() == 1
    assert wls_models.Order.objects.count() == 2
    assert wls_models.Job.objects.count() == 1


def test_a_name_too_long_once_the_prefix_is_added_is_refused(client, page, superuser, smartling_settings):
    smartling_settings.JOB_NAME_PREFIX = "x" * 70
    client.force_login(superuser)

    response = client.post(submit_url(page), submit_data(page, how_to_send_choice="new", new_order_name="W" * 100))

    assert "too long" in response.content.decode()
    assert "new_order_name" in component_form_errors(response)
    assert wls_models.Order.objects.count() == 0
    assert wls_models.Job.objects.count() == 0


def test_a_name_the_field_itself_refuses_is_reported_only_once(client, page, superuser):
    client.force_login(superuser)

    response = client.post(submit_url(page), submit_data(page, how_to_send_choice="new", new_order_name="W" * 150))

    content = response.content.decode()
    assert "at most 100 characters" in content
    assert "Give the order a name." not in content
    assert wls_models.Order.objects.count() == 0


def test_a_new_order_is_not_created_when_every_locale_is_excluded(client, page, superuser, smartling_settings):
    smartling_settings.EXCLUDE_LOCALES = ["fr", "de"]
    client.force_login(superuser)

    client.post(submit_url(page), submit_data(page, how_to_send_choice="new", new_order_name="WNP 157"))

    assert wls_models.Order.objects.count() == 0
    assert wls_models.Job.objects.count() == 0


def test_how_to_send_choice_renders_above_the_fields_it_controls(client, page, superuser):
    OrderFactory(submitted_at=None, created_by=superuser, name="WNP 157")
    client.force_login(superuser)

    content = client.get(submit_url(page)).content.decode()

    assert content.index("how_to_send_choice") < content.index("new_order_name")
    assert content.index("how_to_send_choice") < content.index("new_order_notes")
    assert content.index("new_order_name") < content.index(f'name="{COMPONENT_PREFIX}-order"')


def test_a_subtree_goes_into_one_order(client, page, superuser, smartling_project):
    order = OrderFactory(submitted_at=None, created_by=superuser, name="WNP 157")
    add_children(page, 3)
    client.force_login(superuser)
    assert wls_models.Order.objects.count() == 1
    assert order.order_items.count() == 0

    client.post(
        submit_url(page),
        submit_data(
            page, how_to_send_choice="existing", order=order.pk, new_order_name=page.title, include_subtree=True
        ),
    )

    assert wls_models.Order.objects.count() == 1
    assert order.order_items.count() == 4
    assert wls_models.Job.objects.count() == 0


def test_a_subtree_starting_a_new_order_creates_only_one_order(client, page, superuser, smartling_project):
    add_children(page, 3)
    client.force_login(superuser)
    assert wls_models.Order.objects.count() == 0

    client.post(
        submit_url(page),
        submit_data(page, how_to_send_choice="new", new_order_name="WNP 157", include_subtree=True),
    )

    order = wls_models.Order.objects.get()
    assert order.order_items.count() == 4
    assert wls_models.Job.objects.count() == 0


def test_a_subtree_whose_order_is_sent_midway_is_not_lost(client, page, superuser, smartling_project, mocker):
    order = OrderFactory(submitted_at=None, created_by=superuser, name="WNP 157")
    add_children(page, 3)
    client.force_login(superuser)
    # Make sure that the subtree is queued to join the order.
    held = hold_the_subtree_task(mocker)

    assert wls_models.Order.objects.count() == 1

    # The user requests to add the subtree to the open order.
    client.post(
        submit_url(page),
        submit_data(
            page, how_to_send_choice="existing", order=order.pk, new_order_name=page.title, include_subtree=True
        ),
    )
    # The order gets submitted before the background task can add the subtree to it.
    order.submit(user=superuser)
    assert len(held) == 1
    # The background task processes the subtree.
    held[0]()

    # The order went to Smartling holding only the page itself. Each stranded
    # descendant follows as its own job.
    assert order.order_items.count() == 1
    assert wls_models.Job.objects.exclude(order=order).count() == 3
    assert wls_models.Order.objects.count() == 4
    assert [job.order.order_items.count() for job in wls_models.Job.objects.exclude(order=order)] == [1, 1, 1]


def test_the_form_gives_the_editor_both_numbers_for_a_subtree(client, page, superuser):
    order = OrderFactory(submitted_at=None, created_by=superuser, name="WNP 157")
    add_page_to_order(order, page, superuser)
    add_children(page, 3)
    client.force_login(superuser)

    content = page_text(client.get(submit_url(page)))

    assert "This page and its subtree are 4 pages." in content
    assert "WNP 157 — 1 page" in content


def test_the_subtree_size_is_stated_even_with_no_order_to_join(client, page, superuser):
    add_children(page, 3)
    client.force_login(superuser)

    content = page_text(client.get(submit_url(page)))

    assert "This page and its subtree are 4 pages." in content


def test_nothing_is_said_about_a_subtree_for_a_page_with_no_descendants(client, page, superuser):
    order = OrderFactory(submitted_at=None, created_by=superuser, name="WNP 157")
    add_page_to_order(order, page, superuser)
    client.force_login(superuser)

    content = page_text(client.get(submit_url(page)))

    assert "its subtree" not in content
    assert "WNP 157 — 1 page" in content


def test_sending_a_subtree_now_is_refused(client, page, superuser, smartling_project):
    add_children(page, 1)
    client.force_login(superuser)

    response = client.post(submit_url(page), submit_data(page, include_subtree=True))

    expected_error_text = (
        "A subtree can not be sent right now. To send the subtree with this "
        "page, either add the page and its subtree to an open order, or start "
        "a new order for the page and its subtree."
    )
    assert expected_error_text in page_text(response)
    assert "how_to_send_choice" in component_form_errors(response)
    assert wls_models.Job.objects.count() == 0
    assert wls_models.Order.objects.count() == 0


def test_sending_a_page_with_descendants_now_is_allowed_without_the_subtree(client, page, superuser, smartling_project):
    add_children(page, 1)
    client.force_login(superuser)
    assert wls_models.Job.objects.count() == 0
    assert wls_models.Order.objects.count() == 0

    client.post(submit_url(page), submit_data(page, new_order_name=page.title))

    assert wls_models.Job.objects.count() == 1
    assert wls_models.Order.objects.count() == 1


def test_sending_now_is_allowed_with_the_prefilled_name_untouched(client, page, superuser, smartling_project):
    add_children(page, 1)
    client.force_login(superuser)
    assert wls_models.Job.objects.count() == 0
    assert wls_models.Order.objects.count() == 0

    client.post(submit_url(page), submit_data(page, new_order_name=page.title))

    assert wls_models.Job.objects.count() == 1
    assert wls_models.Order.objects.count() == 1


def test_a_new_order_for_a_subtree_is_named_after_its_root_page(client, page, superuser):
    add_children(page, 1)
    client.force_login(superuser)

    response = client.get(submit_url(page))

    assert component_form(response)["new_order_name"].value() == page.title
    assert f'value="{page.title}"' in page_text(response)


def test_a_page_without_descendants_does_not_prefill_an_order_name(client, page, superuser):
    client.force_login(superuser)

    response = client.get(submit_url(page))

    assert not component_form(response)["new_order_name"].value()


def test_a_name_already_taken_is_not_offered_for_a_subtree(client, page, superuser):
    # Offering a name the form would then refuse sends the editor round a
    # loop they did not start.

    OrderFactory(submitted_at=None, created_by=superuser, name=page.title)
    add_children(page, 1)
    client.force_login(superuser)

    response = client.get(submit_url(page))

    assert not component_form(response)["new_order_name"].value()


def test_a_subtree_whose_order_is_deleted_midway_is_not_lost(client, page, superuser, smartling_project, mocker):
    order = OrderFactory(submitted_at=None, created_by=superuser, name="WNP 157")
    add_children(page, 3)
    client.force_login(superuser)
    # Make sure that the subtree is queued to join the order.
    held = hold_the_subtree_task(mocker)
    assert wls_models.Order.objects.count() == 1

    client.post(
        submit_url(page),
        submit_data(
            page, how_to_send_choice="existing", order=order.pk, new_order_name=page.title, include_subtree=True
        ),
    )
    # The order gets deleted before the background task can add the subtree to it.
    wls_models.Order.objects.filter(pk=order.pk).delete()
    assert wls_models.Order.objects.count() == 0
    # The background task processes the subtree.
    held[0]()

    # Each stranded descendant follows as its own job.
    assert wls_models.Job.objects.count() == 3
    assert wls_models.Order.objects.count() == 3
    assert wls_models.Order.objects.filter(name="WNP 157").count() == 0


def test_an_order_sent_while_the_form_was_open_is_refused_with_a_choice(client, page, superuser, smartling_project):
    order = OrderFactory(submitted_at=None, created_by=superuser, name="WNP 157")
    client.force_login(superuser)
    data = submit_data(page, how_to_send_choice="existing", order=order.pk)
    assert wls_models.Order.objects.count() == 1

    # Someone else sends the order between this form being rendered and submitted.
    order.submitted_at = timezone.now()
    order.save(update_fields=["submitted_at"])
    response = client.post(submit_url(page), data)

    content = page_text(response)
    assert "WNP 157 was sent to Smartling while you were on this page" in content
    # What the editor is told replaces the dropdown's own refusal rather than joining it.
    assert "not one of the available choices" not in content
    assert "Choose an order, or start a new one." not in content
    # The order is what went wrong, so that is where the editor is sent to fix it.
    assert list(component_form_errors(response)) == ["order"]
    assert order.order_items.count() == 0
    assert wls_models.Job.objects.count() == 0
    assert wls_models.Order.objects.count() == 1


def test_an_order_sent_mid_edit_is_refused_while_other_orders_stay_open(client, page, superuser, smartling_project):
    sent_order = OrderFactory(submitted_at=None, created_by=superuser, name="WNP 157")
    OrderFactory(submitted_at=None, created_by=superuser, name="WNP 158")
    client.force_login(superuser)
    data = submit_data(page, how_to_send_choice="existing", order=sent_order.pk)

    sent_order.submitted_at = timezone.now()
    sent_order.save(update_fields=["submitted_at"])
    response = client.post(submit_url(page), data)

    assert "WNP 157 was sent to Smartling while you were on this page" in page_text(response)
    # The message above names the sent order, so only the dropdown itself can
    # say which orders are still on offer.
    offered_open_orders = orders_in_open_orders_dropdown(response)
    assert not any(offer.startswith("WNP 157") for offer in offered_open_orders)
    assert any(offer.startswith("WNP 158") for offer in offered_open_orders)
    assert wls_models.OrderItem.objects.count() == 0
    assert wls_models.Job.objects.count() == 0


def test_a_subtree_is_not_told_to_send_now_when_its_order_is_sent_mid_edit(client, page, superuser, smartling_project):
    add_children(page, 3)
    order = OrderFactory(submitted_at=None, created_by=superuser, name="WNP 157")
    client.force_login(superuser)
    data = submit_data(page, how_to_send_choice="existing", order=order.pk, include_subtree=True)

    order.submitted_at = timezone.now()
    order.save(update_fields=["submitted_at"])
    response = client.post(submit_url(page), data)

    content = page_text(response)
    assert "WNP 157 was sent to Smartling while you were on this page" in content
    # Sending a subtree as its own job is refused, so it is not advised here.
    assert "send this page as its own job" not in content
    assert wls_models.OrderItem.objects.count() == 0
    assert wls_models.Job.objects.count() == 0


def test_an_order_deleted_mid_edit_does_not_leave_an_empty_dropdown(client, page, superuser, smartling_project):
    order = OrderFactory(submitted_at=None, created_by=superuser, name="WNP 157")
    client.force_login(superuser)
    data = submit_data(page, how_to_send_choice="existing", order=order.pk)

    wls_models.Order.objects.filter(pk=order.pk).delete()
    response = client.post(submit_url(page), data)

    # With no order left to join, the choice that needs one is not on offer.
    assert "Add to an open order" not in page_text(response)
    assert wls_models.OrderItem.objects.count() == 0
    assert wls_models.Job.objects.count() == 0


def test_the_form_keeps_the_markup_the_order_choice_script_reaches_into(client, page, superuser):
    """
    The script finds the rows to hide in markup that belongs to Wagtail and to
    wagtail-localize, so an upgrade that renames any of it would leave every
    field on screen with nothing to say so.
    """
    OrderFactory(submitted_at=None, created_by=superuser, name="WNP 157")
    add_children(page, 1)
    client.force_login(superuser)

    content = client.get(submit_url(page)).content.decode()
    # The order-choice.js is in the page content
    assert "order-choice.js" in content

    soup = BeautifulSoup(content, "html.parser")
    # Each field the choice governs sits in a row that can be hidden whole,
    # taking its label with it.
    for field_name in ("new_order_name", "new_order_notes", "order"):
        field_input = soup.select_one(f"#id_{COMPONENT_PREFIX}-{field_name}")
        assert field_input is not None, field_name
        assert field_input.find_parent("li") is not None, field_name

    # Every option of the choice is wrapped on its own, so that "Send now" can
    # be taken off screen without the other two.
    send_now = soup.select_one(f'input[name="{COMPONENT_PREFIX}-how_to_send_choice"][value="now"]')
    assert send_now is not None
    option_wrapper = send_now.find_parent("div")
    assert len(option_wrapper.select(f'input[name="{COMPONENT_PREFIX}-how_to_send_choice"]')) == 1

    assert soup.select_one('input[name="include_subtree"]') is not None


def test_a_refused_field_is_marked_the_way_the_order_choice_script_looks_for(client, page, superuser):
    """A row the form re-renders with an error stays on screen, so it has to be findable."""
    client.force_login(superuser)

    response = client.post(submit_url(page), submit_data(page, how_to_send_choice="new", new_order_name=""))

    soup = BeautifulSoup(response.content.decode(), "html.parser")
    refused_row = soup.select_one(f"#id_{COMPONENT_PREFIX}-new_order_name").find_parent("li")
    assert refused_row.select_one(".error-message") is not None
