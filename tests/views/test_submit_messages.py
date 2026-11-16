"""
Tests for the confirmation an editor sees after submitting a page for
translation, which has to say whether the page actually went to Smartling.
"""

from html import unescape

import pytest

from bs4 import BeautifulSoup
from django.contrib.auth.models import Permission
from django.urls import resolve, reverse
from wagtail.models import Locale

from wagtail_localize_smartling import models as wls_models
from wagtail_localize_smartling.views import (
    SmartlingSubmitPageTranslationView,
    SmartlingSubmitSnippetTranslationView,
)

from testapp.factories import InfoPageFactory, InfoSnippetFactory, UserFactory
from tests.factories import OrderFactory


pytestmark = pytest.mark.django_db

COMPONENT_PREFIX = f"component-{wls_models.Job._meta.db_table}"


@pytest.fixture()
def page(root_page):
    return InfoPageFactory(parent=root_page, title="Component test page")


@pytest.fixture()
def submitter_user():
    """Someone who may send pages for translation but may not change an order."""
    user = UserFactory(is_superuser=False)
    user.user_permissions.clear()
    user.user_permissions.add(
        Permission.objects.get(codename="access_admin"),
        Permission.objects.get(codename="submit_translation"),
    )
    return user


def submit_url(page):
    return reverse("wagtail_localize:submit_page_translation", kwargs={"page_id": page.pk})


def submit_data(page, **component_fields):
    data = {
        "locales": list(Locale.objects.exclude(pk=page.locale_id).values_list("pk", flat=True)),
        f"{COMPONENT_PREFIX}-enabled": True,
        f"{COMPONENT_PREFIX}-due_date": "",
    }
    data.update({f"{COMPONENT_PREFIX}-{name}": value for name, value in component_fields.items()})
    return data


def messages_markup(response):
    """The admin's confirmation banner, exactly as it is served."""
    soup = BeautifulSoup(response.content.decode(), "html.parser")
    return "".join(str(item) for item in soup.select("div.messages li"))


def messages_shown(response):
    """The confirmation banner as an editor reads it, escaping undone."""
    return unescape(messages_markup(response))


def test_sending_now_keeps_wagtail_localize_s_message(client, page, superuser, smartling_project):
    client.force_login(superuser)
    assert wls_models.Job.objects.count() == 0

    response = client.post(submit_url(page), submit_data(page), follow=True)

    assert "was successfully submitted for translation" in messages_shown(response)
    assert wls_models.Job.objects.count() == 1


def test_adding_to_an_open_order_says_it_has_not_been_sent(client, page, superuser):
    order = OrderFactory(submitted_at=None, created_by=superuser, name="WNP 157")
    client.force_login(superuser)

    response = client.post(
        submit_url(page), submit_data(page, how_to_send_choice="existing", order=order.pk), follow=True
    )

    content = messages_shown(response)
    assert "The page 'Component test page' was added to order 'WNP 157'" in content
    assert "has not been sent to Smartling yet" in content
    assert "was successfully submitted for translation" not in content
    assert reverse("wagtail_localize_smartling_jobs:edit", args=(order.pk,)) in content
    assert order.order_items.count() == 1
    assert wls_models.Job.objects.count() == 0


def test_starting_a_new_order_states_that_it_was_started(client, page, superuser):
    client.force_login(superuser)

    response = client.post(
        submit_url(page), submit_data(page, how_to_send_choice="new", new_order_name="WNP 158"), follow=True
    )

    order = wls_models.Order.objects.get()
    content = messages_shown(response)
    assert "was added to order 'WNP 158'" in content
    # The user gets a link to edit the order.
    assert reverse("wagtail_localize_smartling_jobs:edit", args=(order.pk,)) in content


def test_someone_who_cannot_send_an_order_is_not_told_to(client, page, submitter_user):
    order = OrderFactory(submitted_at=None, created_by=submitter_user, name="WNP 157")
    client.force_login(submitter_user)

    response = client.post(
        submit_url(page), submit_data(page, how_to_send_choice="existing", order=order.pk), follow=True
    )

    content = messages_shown(response)
    assert "has not been sent to Smartling yet" in content
    assert "Review and send the order" not in content
    assert "Someone who manages Smartling orders will need to send it." in content
    assert reverse("wagtail_localize_smartling_jobs:edit", args=(order.pk,)) not in content
    assert order.order_items.count() == 1


def test_a_page_that_never_reached_the_order_is_not_claimed_for_it(client, page, superuser, smartling_settings):
    # fr and de are the only locales there are to translate into, so excluding
    # both leaves the submission with nothing to do: the order is chosen, and
    # no page ever joins it.
    smartling_settings.EXCLUDE_LOCALES = ["fr", "de"]
    order = OrderFactory(submitted_at=None, created_by=superuser, name="WNP 157")
    client.force_login(superuser)

    response = client.post(
        submit_url(page), submit_data(page, how_to_send_choice="existing", order=order.pk), follow=True
    )

    assert order.order_items.count() == 0
    assert "was added to order" not in messages_shown(response)


def test_turning_the_component_off_keeps_wagtail_localize_s_message(client, page, superuser):
    OrderFactory(submitted_at=None, created_by=superuser, name="WNP 157")
    client.force_login(superuser)
    data = submit_data(page)
    del data[f"{COMPONENT_PREFIX}-enabled"]

    response = client.post(submit_url(page), data, follow=True)

    assert "was successfully submitted for translation" in messages_shown(response)
    assert wls_models.OrderItem.objects.count() == 0


def test_an_order_name_cannot_smuggle_markup_into_the_banner(client, page, superuser):
    order = OrderFactory(submitted_at=None, created_by=superuser, name="<script>alert(1)</script>")
    client.force_login(superuser)

    response = client.post(
        submit_url(page), submit_data(page, how_to_send_choice="existing", order=order.pk), follow=True
    )

    assert "<script>" not in messages_markup(response)
    assert order.name in messages_shown(response)


def test_a_snippet_added_to_an_order_is_reported_the_same_way(client, superuser):
    snippet = InfoSnippetFactory(content="Test snippet")
    order = OrderFactory(submitted_at=None, created_by=superuser, name="WNP 157")
    client.force_login(superuser)
    url = reverse(
        "wagtail_localize:submit_snippet_translation",
        kwargs={"app_label": "testapp", "model_name": "infosnippet", "pk": snippet.pk},
    )

    data = {
        "locales": list(Locale.objects.exclude(pk=snippet.locale_id).values_list("pk", flat=True)),
        f"{COMPONENT_PREFIX}-enabled": True,
        f"{COMPONENT_PREFIX}-due_date": "",
        f"{COMPONENT_PREFIX}-how_to_send_choice": "existing",
        f"{COMPONENT_PREFIX}-order": order.pk,
    }
    response = client.post(url, data, follow=True)

    content = messages_shown(response)
    # Snippets keep the model name wagtail-localize's own message carries.
    assert f"The info snippet '{snippet}' was added to order 'WNP 157'" in content
    assert "has not been sent to Smartling yet" in content
    assert order.order_items.count() == 1


@pytest.mark.parametrize(
    ("url_name", "kwargs", "view_class"),
    [
        ("submit_page_translation", {"page_id": 1}, SmartlingSubmitPageTranslationView),
        (
            "submit_snippet_translation",
            {"app_label": "testapp", "model_name": "infosnippet", "pk": 1},
            SmartlingSubmitSnippetTranslationView,
        ),
    ],
)
def test_the_url_wagtail_localize_advertises_leads_here(url_name, kwargs, view_class):
    # Editors reach these URLs through wagtail-localize's own buttons
    match = resolve(reverse(f"wagtail_localize:{url_name}", kwargs=kwargs))

    assert match.func.view_class is view_class
