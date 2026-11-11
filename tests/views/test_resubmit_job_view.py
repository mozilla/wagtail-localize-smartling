import html

import pytest

from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone
from pytest_django.asserts import assertRedirects

from wagtail_localize_smartling.api.types import JobStatus
from wagtail_localize_smartling.models import Job, Order
from wagtail_localize_smartling.templatetags.wagtail_localize_smartling_admin_tags import (
    smartling_job_url,
)

from tests.factories import OrderFactory, cancel_at_smartling, submit_order_of_pages


pytestmark = pytest.mark.django_db


WAGTAIL_ADMIN_HOME = reverse("wagtailadmin_home")
ORDERS_INDEX = reverse("wagtail_localize_smartling_jobs:index")


def resubmit_url(job) -> str:
    return reverse("wagtail_localize_smartling_retry_job", args=[job.pk])


def reported_messages(response) -> str:
    """The messages as the screen reads them, with the markup's escaping undone."""
    return " ".join(" ".join(html.unescape(str(message)).split()) for message in get_messages(response.wsgi_request))


@pytest.fixture
def cancelled_smartling_job(smartling_job):
    smartling_job.status = JobStatus.CANCELLED
    smartling_job.save(update_fields=["status"])
    return smartling_job


def test_get_resubmit_job_view_denied_if_job_not_in_untranslated_status(client, superuser, smartling_job):
    client.force_login(superuser)
    url = reverse("wagtail_localize_smartling_retry_job", args=[smartling_job.id])
    response = client.get(url)
    assertRedirects(response, WAGTAIL_ADMIN_HOME)


def test_get_resubmit_job_denied_without_appropriate_user_permission(client, regular_user, cancelled_smartling_job):
    client.force_login(regular_user)
    url = reverse("wagtail_localize_smartling_retry_job", args=[cancelled_smartling_job.id])
    response = client.get(url)
    assertRedirects(response, WAGTAIL_ADMIN_HOME)


def test_get_resubmit_job_denied_if_newer_job_exists(client, regular_user, cancelled_smartling_job):
    new_job = cancelled_smartling_job
    new_job.id = None
    new_job.status = JobStatus.DRAFT
    new_job.save()

    client.force_login(regular_user)
    url = reverse("wagtail_localize_smartling_retry_job", args=[cancelled_smartling_job.id])
    response = client.get(url)
    assertRedirects(response, WAGTAIL_ADMIN_HOME)


def test_get_resubmit_job_with_allowed_user_permission_succeeds(client, smartling_reporter, cancelled_smartling_job):
    client.force_login(smartling_reporter)
    url = reverse("wagtail_localize_smartling_retry_job", args=[cancelled_smartling_job.id])
    response = client.get(url)
    assert response.status_code == 200

    assert cancelled_smartling_job.order.name in response.content.decode()


def test_get_resubmit_job_view(client, superuser, cancelled_smartling_job):
    client.force_login(superuser)
    url = reverse("wagtail_localize_smartling_retry_job", args=[cancelled_smartling_job.id])
    response = client.get(url)
    assert response.status_code == 200

    content = response.content.decode()
    assert cancelled_smartling_job.order.name in content
    assert smartling_job_url(cancelled_smartling_job) in content


def test_post_resubmit_job_view_creates_a_new_job(client, superuser, cancelled_smartling_job):
    assert Job.objects.count() == 1

    client.force_login(superuser)
    url = reverse("wagtail_localize_smartling_retry_job", args=[cancelled_smartling_job.id])
    response = client.post(url, {"job_name": "Second attempt"})
    assert response.status_code == 302

    cancelled_smartling_job.refresh_from_db()
    assert cancelled_smartling_job.status == JobStatus.CANCELLED.name

    assert Job.objects.count() == 2
    assert Job.objects.order_by("pk").last().status == JobStatus.UNSYNCED.name  # pyright: ignore[reportOptionalMemberAccess]


def test_resubmit_non_existent_job_404s(client, superuser):
    client.force_login(superuser)
    url = reverse("wagtail_localize_smartling_retry_job", args=[999999])
    response = client.get(url)
    assert response.status_code == 302
    # bit of a hack, but in this case, we raise a 404
    # but the i18n setup tries with the default language prefixed
    assert response.url == f"/en{url}"


def test_resubmitting_sends_only_the_locales_that_never_landed(client, superuser, root_page, smartling_project):
    order = submit_order_of_pages(root_page, superuser)
    job = order.jobs.get()
    cancel_at_smartling(job)
    landed = job.job_translations.first()
    landed.imported_at = timezone.now()
    landed.save(update_fields=["imported_at"])
    still_pending = job.job_translations.filter(imported_at__isnull=True)
    assert still_pending.exists(), "The order has to have something left to send for this to mean anything"
    client.force_login(superuser)

    client.post(resubmit_url(job), {"job_name": order.suggested_job_name()})

    new_job = order.jobs.exclude(pk=job.pk).get()
    new_job_translations_locale_ids = new_job.translations.values_list("target_locale_id", flat=True)
    still_pending_translations_locale_ids = still_pending.values_list("translation__target_locale_id", flat=True)
    assert list(new_job_translations_locale_ids) == list(still_pending_translations_locale_ids)
    assert landed.translation not in new_job.translations.all()


def test_resubmitting_carries_every_page_of_the_order(client, superuser, root_page, smartling_project):
    order = submit_order_of_pages(root_page, superuser, page_count=3)
    job = order.jobs.get()
    cancel_at_smartling(job)
    assert not job.job_translations.filter(imported_at__isnull=False).exists(), (
        "Nothing has landed, so every page has to be carried over"
    )
    client.force_login(superuser)

    client.post(resubmit_url(job), {"job_name": order.suggested_job_name()})

    new_job = order.jobs.exclude(pk=job.pk).get()
    assert new_job.files.count() == 3


def test_a_page_whose_locales_all_landed_is_left_out_of_the_resend(client, superuser, root_page, smartling_project):
    """A file with no strings to authorize leaves the whole job stuck at Smartling."""
    order = submit_order_of_pages(root_page, superuser, page_count=2)
    job = order.jobs.get()
    cancel_at_smartling(job)
    finished_source = order.order_items.first().translation_source
    job.job_translations.filter(translation__source=finished_source).update(imported_at=timezone.now())
    client.force_login(superuser)

    response = client.post(resubmit_url(job), {"job_name": order.suggested_job_name()})

    new_job = order.jobs.exclude(pk=job.pk).get()
    assert new_job.files.count() == 1
    assert finished_source.pk not in set(new_job.files.values_list("translation_source_id", flat=True))
    assert finished_source.object_repr in reported_messages(response)


def test_a_submission_blocked_before_it_reached_smartling_can_be_resubmitted(
    client, superuser, root_page, smartling_project
):
    order = submit_order_of_pages(root_page, superuser)
    job = order.jobs.get()
    job.record_sync_failure(ValueError("duplicate name"), permanent=True, pushing=True)
    client.force_login(superuser)

    response = client.post(resubmit_url(job), {"job_name": "Second attempt"})

    assert response.status_code == 302
    assert order.jobs.count() == 2


def test_only_the_latest_job_on_the_order_can_be_resubmitted(client, superuser, root_page, smartling_project):
    order = submit_order_of_pages(root_page, superuser)
    old_job = order.jobs.get()
    cancel_at_smartling(old_job)
    order.submit(user=superuser, job_name="Second attempt")
    client.force_login(superuser)
    assert order.jobs.count() == 2

    response = client.post(resubmit_url(old_job), {"job_name": "Third attempt"})

    assertRedirects(response, WAGTAIL_ADMIN_HOME)
    assert order.jobs.count() == 2


def test_a_job_that_belongs_to_no_order_cannot_be_resubmitted(client, superuser, cancelled_smartling_job):
    cancelled_smartling_job.order = None
    cancelled_smartling_job.save(update_fields=["order"])
    client.force_login(superuser)
    assert Order.objects.count() == 1
    assert Job.objects.count() == 1

    response = client.post(resubmit_url(cancelled_smartling_job), {"job_name": "Second attempt"})

    assertRedirects(response, WAGTAIL_ADMIN_HOME)
    assert Order.objects.count() == 1
    assert Job.objects.count() == 1


def test_the_resend_name_is_suggested_and_editable(client, superuser, root_page, smartling_project):
    order = submit_order_of_pages(root_page, superuser)
    job = order.jobs.get()
    cancel_at_smartling(job)
    client.force_login(superuser)

    content = client.get(resubmit_url(job)).content.decode()
    assert f"{order.name}-2" in content

    client.post(resubmit_url(job), {"job_name": "WNP 157-rerun"})

    assert order.jobs.exclude(pk=job.pk).get().name == "WNP 157-rerun"


def test_a_name_another_job_already_has_is_refused(client, superuser, root_page, smartling_project):
    order = submit_order_of_pages(root_page, superuser)
    job = order.jobs.get()
    cancel_at_smartling(job)
    client.force_login(superuser)

    response = client.post(resubmit_url(job), {"job_name": job.name})

    assert response.status_code == 200
    assert "already called" in response.content.decode()
    assert order.jobs.count() == 1


def test_a_resend_with_nothing_left_to_send_says_so_instead_of_failing(client, superuser, root_page, smartling_project):
    order = submit_order_of_pages(root_page, superuser)
    job = order.jobs.get()
    cancel_at_smartling(job)
    job.job_translations.update(imported_at=timezone.now())
    client.force_login(superuser)

    response = client.post(resubmit_url(job), {"job_name": "Second attempt"})

    assert order.jobs.count() == 1
    assert "nothing left to send" in reported_messages(response)


def test_the_confirmation_screen_names_the_order_its_pages_and_its_locales(
    client, superuser, root_page, smartling_project
):
    order = submit_order_of_pages(root_page, superuser, page_count=2)
    job = order.jobs.get()
    cancel_at_smartling(job)
    client.force_login(superuser)

    content = client.get(resubmit_url(job)).content.decode()

    assert order.name in content
    assert "2 pages" in content
    assert "French" in content
    assert "German" in content


def test_a_locale_the_editor_has_stopped_is_not_sent_again(client, superuser, root_page, smartling_project):
    order = submit_order_of_pages(root_page, superuser)
    job = order.jobs.get()
    cancel_at_smartling(job)
    stopped = job.translations.first()
    stopped.enabled = False
    stopped.save(update_fields=["enabled"])
    client.force_login(superuser)

    client.post(resubmit_url(job), {"job_name": "Second attempt"})

    new_job = order.jobs.exclude(pk=job.pk).get()
    assert stopped not in new_job.translations.all()
    assert new_job.translations.count() == job.translations.count() - 1


def test_the_confirmation_screen_is_not_shown_when_nothing_is_left_to_send(
    client, superuser, root_page, smartling_project
):
    order = submit_order_of_pages(root_page, superuser)
    job = order.jobs.get()
    cancel_at_smartling(job)
    job.job_translations.update(imported_at=timezone.now())
    client.force_login(superuser)

    response = client.get(resubmit_url(job))

    assert "nothing left to send" in reported_messages(response)
    assertRedirects(response, ORDERS_INDEX)


def test_a_resend_keeps_the_configured_job_name_prefix(
    client, superuser, root_page, smartling_project, smartling_settings
):
    smartling_settings.JOB_NAME_PREFIX = "WWW"
    order = submit_order_of_pages(root_page, superuser)
    job = order.jobs.get()
    cancel_at_smartling(job)
    client.force_login(superuser)

    assert f"WWW {order.name}-2" in client.get(resubmit_url(job)).content.decode()

    refused = client.post(resubmit_url(job), {"job_name": "Rerun by hand"})

    assert refused.status_code == 200
    assert order.jobs.count() == 1

    client.post(resubmit_url(job), {"job_name": "WWW Rerun by hand"})

    assert order.jobs.exclude(pk=job.pk).get().name == "WWW Rerun by hand"


def test_a_resend_under_the_name_of_another_order_is_refused(
    client, superuser, root_page, smartling_project, smartling_settings
):
    smartling_settings.JOB_NAME_PREFIX = "WWW"
    order = submit_order_of_pages(root_page, superuser)
    job = order.jobs.get()
    cancel_at_smartling(job)
    waiting = OrderFactory(submitted_at=None, created_by=superuser, name="WNP 158")
    client.force_login(superuser)

    response = client.post(resubmit_url(job), {"job_name": f"WWW {waiting.name}"})

    assert response.status_code == 200
    assert "already called" in response.content.decode()
    assert order.jobs.count() == 1


def test_a_successful_resend_says_the_order_went_to_smartling(client, superuser, root_page, smartling_project):
    order = submit_order_of_pages(root_page, superuser)
    job = order.jobs.get()
    cancel_at_smartling(job)
    client.force_login(superuser)

    response = client.post(resubmit_url(job), {"job_name": "Second attempt"})

    assert "was resent to Smartling" in reported_messages(response)
