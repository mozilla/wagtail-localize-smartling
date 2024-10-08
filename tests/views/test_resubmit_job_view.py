import pytest

from django.urls import reverse
from django.utils import timezone
from pytest_django.asserts import assertRedirects
from wagtail.models import Locale
from wagtail_localize.models import Translation, TranslationSource

from wagtail_localize_smartling.api.types import JobStatus
from wagtail_localize_smartling.models import Job
from wagtail_localize_smartling.templatetags.wagtail_localize_smartling_admin_tags import (  # noqa: E501
    smartling_job_url,
)
from wagtail_localize_smartling.utils import compute_content_hash

from testapp.factories import InfoPageFactory


pytestmark = pytest.mark.django_db


WAGTAIL_ADMIN_HOME = reverse("wagtailadmin_home")


@pytest.fixture
def smartling_job(smartling_project, superuser, root_page):
    page = InfoPageFactory(parent=root_page, title="Component test page")
    translation_source, created = TranslationSource.get_or_create_from_instance(page)
    page_translation = Translation.objects.create(
        source=translation_source,
        target_locale=Locale.objects.get(language_code="fr"),
    )

    now = timezone.now()
    job = Job.objects.create(
        project=smartling_project,
        translation_source=translation_source,
        user=superuser,
        name=Job.get_default_name(translation_source, [page_translation]),
        description=Job.get_default_description(translation_source, [page_translation]),
        reference_number=Job.get_default_reference_number(
            translation_source, [page_translation]
        ),
        content_hash=compute_content_hash(translation_source.export_po()),
        first_synced_at=now,
        last_synced_at=now,
        status=JobStatus.DRAFT,
        translation_job_uid="job_to_be_cancelled",
    )
    job.translations.set([page_translation])

    return job


@pytest.fixture
def cancelled_smartling_job(smartling_job):
    smartling_job.status = JobStatus.CANCELLED
    smartling_job.save(update_fields=["status"])
    return smartling_job


def test_get_resubmit_job_view_denied_if_job_not_in_untranslated_status(
    client, superuser, smartling_job
):
    client.force_login(superuser)
    url = reverse("wagtail_localize_smartling_retry_job", args=[smartling_job.id])
    response = client.get(url)
    assertRedirects(response, WAGTAIL_ADMIN_HOME)


def test_get_resubmit_job_denied_without_appropriate_user_permission(
    client, regular_user, cancelled_smartling_job
):
    client.force_login(regular_user)
    url = reverse(
        "wagtail_localize_smartling_retry_job", args=[cancelled_smartling_job.id]
    )
    response = client.get(url)
    assertRedirects(response, WAGTAIL_ADMIN_HOME)


def test_get_resubmit_job_denied_if_newer_job_exists(
    client, regular_user, cancelled_smartling_job
):
    new_job = cancelled_smartling_job
    new_job.id = None
    new_job.status = JobStatus.DRAFT
    new_job.save()

    client.force_login(regular_user)
    url = reverse(
        "wagtail_localize_smartling_retry_job", args=[cancelled_smartling_job.id]
    )
    response = client.get(url)
    assertRedirects(response, WAGTAIL_ADMIN_HOME)


def test_get_resubmit_job_with_allowed_user_permission_succeeds(
    client, smartling_reporter, cancelled_smartling_job
):
    client.force_login(smartling_reporter)
    url = reverse(
        "wagtail_localize_smartling_retry_job", args=[cancelled_smartling_job.id]
    )
    response = client.get(url)
    assert response.status_code == 200

    assert "Are you sure you want to resubmit this job" in response.content.decode()


def test_get_resubmit_job_view(client, superuser, cancelled_smartling_job):
    client.force_login(superuser)
    url = reverse(
        "wagtail_localize_smartling_retry_job", args=[cancelled_smartling_job.id]
    )
    response = client.get(url)
    assert response.status_code == 200

    content = response.content.decode()
    assert "Are you sure you want to resubmit this job" in content
    assert smartling_job_url(cancelled_smartling_job) in content


def test_post_resubmit_job_view_creates_a_new_job(
    client, superuser, cancelled_smartling_job
):
    assert Job.objects.count() == 1

    client.force_login(superuser)
    url = reverse(
        "wagtail_localize_smartling_retry_job", args=[cancelled_smartling_job.id]
    )
    response = client.post(url)
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
