import pytest

from django.contrib.auth.models import Permission
from django.urls import reverse

from wagtail_localize_smartling import models as wls_models
from wagtail_localize_smartling.api.types import JobStatus

from testapp.factories import UserFactory
from tests.factories import JobFactory, OrderFactory


pytestmark = pytest.mark.django_db


@pytest.fixture()
def order_viewer():
    """Someone who reads the orders but submits nothing."""
    user = UserFactory(is_superuser=False)
    user.user_permissions.add(Permission.objects.get(codename="access_admin"))
    user.user_permissions.add(
        Permission.objects.get(content_type__app_label="wagtail_localize_smartling", codename="view_order")
    )
    return user


def test_a_sent_order_cannot_be_deleted(client, superuser, root_page):
    """A sent order is the record of what went to Smartling, so it is not editable."""
    sent = OrderFactory(created_by=superuser)
    JobFactory(source_instance=root_page, order=sent, synced=True, status=JobStatus.IN_PROGRESS)
    client.force_login(superuser)

    response = client.post(reverse("wagtail_localize_smartling_jobs:delete", args=(sent.pk,)))

    assert response.status_code == 302
    assert response.url == reverse("wagtailadmin_home")
    assert wls_models.Order.objects.filter(pk=sent.pk).exists()


def test_a_draft_order_can_still_be_deleted(client, superuser):
    draft = OrderFactory(submitted_at=None, created_by=superuser)
    client.force_login(superuser)

    client.post(reverse("wagtail_localize_smartling_jobs:delete", args=(draft.pk,)))

    assert not wls_models.Order.objects.filter(pk=draft.pk).exists()


def test_an_order_cannot_be_created_from_a_blank_admin_form(client, superuser):
    """An order gains its pages on a page's own submit form, never here."""
    client.force_login(superuser)

    response = client.get(reverse("wagtail_localize_smartling_jobs:index"))

    assert reverse("wagtail_localize_smartling_jobs:add") not in response.content.decode()


def test_a_viewer_reaches_a_draft_through_inspect_url(client, order_viewer):
    draft = OrderFactory(submitted_at=None, created_by=order_viewer)
    client.force_login(order_viewer)

    content = client.get(reverse("wagtail_localize_smartling_jobs:index")).content.decode()

    assert reverse("wagtail_localize_smartling_jobs:inspect", args=(draft.pk,)) in content
    assert reverse("wagtail_localize_smartling_jobs:edit", args=(draft.pk,)) not in content
