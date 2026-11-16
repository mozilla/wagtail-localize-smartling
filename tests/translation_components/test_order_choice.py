"""
Tests for the choice between sending a page to Smartling now and collecting it
into an order, offered on wagtail-localize's submit-for-translation form.
"""

from datetime import timedelta
from html import unescape

import pytest

from django.urls import reverse
from django.utils import timezone
from wagtail.models import Locale

from wagtail_localize_smartling import models as wls_models

from testapp.factories import InfoPageFactory
from tests.factories import JobFactory, OrderFactory


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


def component_form_errors(response):
    """The Smartling component's own errors, from a submit page re-rendered after a refusal."""
    component_form = response.context["components"].components[0][2]
    return component_form.errors


def submit_data(page, **component_fields):
    data = {
        "locales": list(Locale.objects.exclude(pk=page.locale_id).values_list("pk", flat=True)),
        f"{COMPONENT_PREFIX}-enabled": True,
        f"{COMPONENT_PREFIX}-due_date": "",
    }
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
