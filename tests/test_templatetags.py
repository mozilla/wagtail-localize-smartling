import pytest

from django.test import RequestFactory
from django.utils import timezone
from wagtail_localize.models import Translation

from wagtail_localize_smartling.templatetags.wagtail_localize_smartling_admin_tags import (
    smartling_edit_translation_message,
)

from tests.factories import cancel_at_smartling, submit_order_of_pages


pytestmark = pytest.mark.django_db


def banner_for(translation) -> str:
    """The warning the translation edit page draws above the editor."""
    context = {"translation": translation, "request": RequestFactory().get("/admin/")}
    return str(smartling_edit_translation_message(context)["message"])


def test_the_banner_offers_a_resubmit_that_names_the_order(superuser, root_page, smartling_project):
    order = submit_order_of_pages(root_page, superuser)
    cancel_at_smartling(order.jobs.get())

    banner = banner_for(order.jobs.get().translations.first())

    assert "cancelled" in banner
    assert "Resubmit" in banner
    assert order.name in banner


def test_the_banner_hides_resubmit_when_the_order_has_moved_on(superuser, root_page, smartling_project):
    order = submit_order_of_pages(root_page, superuser, page_count=2)
    cancelled = order.jobs.get()
    cancel_at_smartling(cancelled)
    landed = cancelled.job_translations.first()
    landed.imported_at = timezone.now()
    landed.save(update_fields=["imported_at"])
    still_pending = Translation.objects.filter(
        pk__in=cancelled.job_translations.filter(imported_at__isnull=True).values("translation")
    )
    order.submit(user=superuser, job_name="Second attempt", translations=still_pending)

    banner = banner_for(landed.translation)

    assert "cancelled" in banner
    assert "resubmit" not in banner.lower()


def test_the_banner_names_a_live_job_without_offering_a_resubmit(superuser, root_page, smartling_project):
    order = submit_order_of_pages(root_page, superuser)

    banner = banner_for(order.jobs.get().translations.first())

    assert "managed by Smartling" in banner
    assert "resubmit" not in banner.lower()


def test_the_banner_hides_resubmit_once_every_locale_has_arrived(superuser, root_page, smartling_project):
    order = submit_order_of_pages(root_page, superuser)
    cancelled = order.jobs.get()
    cancel_at_smartling(cancelled)
    cancelled.job_translations.update(imported_at=timezone.now())

    banner = banner_for(cancelled.translations.first())

    assert "cancelled" in banner
    assert "resubmit" not in banner.lower()
