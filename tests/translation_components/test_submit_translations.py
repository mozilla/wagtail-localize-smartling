"""
Tests for the wagtail-localize translation components functionality when
submitting a page for translation
"""

import pytest

from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.utils import timezone
from pytest_django.asserts import assertRedirects
from wagtail.models import Locale, Page
from wagtail_localize.models import (
    Translation,
    TranslationSource,
    get_edit_url,
)

from wagtail_localize_smartling.api.types import JobStatus
from wagtail_localize_smartling.models import Job

from testapp.factories import InfoPageFactory


pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ["target_locales"],
    [
        [("de",)],
        [("de", "fr")],
    ],
)
def test_submitting_for_translation_with_no_existing_jobs_no_child_pages(
    client, root_page, superuser, target_locales, smartling_project
):
    page = InfoPageFactory(parent=root_page, title="Component test page")

    page_edit_url = get_edit_url(page)
    assert page_edit_url is not None

    target_locale_ids = Locale.objects.values_list("pk", flat=True).filter(
        language_code__in=target_locales
    )

    component_form_prefix = f"component-{Job._meta.db_table}"

    submit_url = reverse(
        "wagtail_localize:submit_page_translation",
        kwargs={"page_id": page.pk},
    )
    client.force_login(superuser)

    get_response = client.get(submit_url)
    assert get_response.status_code == 200

    component_mgr = get_response.context["components"]
    assert len(component_mgr.components) == 1

    post_response = client.post(
        submit_url,
        data={
            "locales": target_locale_ids,
            f"{component_form_prefix}-enabled": True,
            f"{component_form_prefix}-due_date": "",
        },
    )

    translation_source = TranslationSource.objects.get_for_instance(page)  # pyright: ignore[reportAttributeAccessIssue]
    translations = Translation.objects.filter(
        source=translation_source,
        target_locale_id__in=target_locale_ids,
    )
    assert {t.target_locale.language_code for t in translations} == set(target_locales)

    if len(target_locales) > 1:
        redirect_url = reverse(
            "wagtailadmin_explore",
            kwargs={"parent_page_id": page.get_parent().pk},
        )
    else:
        redirect_url = translations[0].get_target_instance_edit_url()

    assert redirect_url is not None
    assertRedirects(post_response, redirect_url)

    job = Job.objects.get(
        first_synced_at=None,
        last_synced_at=None,
        translation_source__object__content_type=ContentType.objects.get_for_model(
            Page
        ),
        translation_source__object__translation_key=page.translation_key,
    )

    assert job.user == superuser
    assert job.translation_source == translation_source
    assert set(job.translations.values_list("pk", flat=True)) == {
        t.pk for t in translations
    }
    assert job.first_synced_at is None
    assert job.last_synced_at is None
    assert job.due_date is None


@pytest.mark.parametrize(
    "status,show_message",
    [
        (JobStatus.UNSYNCED, True),
        (JobStatus.AWAITING_AUTHORIZATION, True),
        (JobStatus.IN_PROGRESS, True),
        (JobStatus.COMPLETED, True),
        (JobStatus.CANCELLED, False),
        (JobStatus.DELETED, False),
    ],
)
def test_managed_by_message_on_translation(
    client, root_page, superuser, smartling_project, status, show_message
):
    client.force_login(superuser)

    page = InfoPageFactory(parent=root_page, title="Component test page")

    target_locale_ids = Locale.objects.values_list("pk", flat=True).filter(
        language_code__in=["de"]
    )

    component_form_prefix = f"component-{Job._meta.db_table}"

    submit_url = reverse(
        "wagtail_localize:submit_page_translation",
        kwargs={"page_id": page.pk},
    )

    post_response = client.post(
        submit_url,
        data={
            "locales": target_locale_ids,
            f"{component_form_prefix}-enabled": True,
            f"{component_form_prefix}-due_date": "",
        },
    )

    translation_source = TranslationSource.objects.get_for_instance(page)  # pyright: ignore[reportAttributeAccessIssue]
    translations = Translation.objects.filter(
        source=translation_source,
        target_locale_id__in=target_locale_ids,
    )
    assert [t.target_locale.language_code for t in translations] == ["de"]

    redirect_url = translations[0].get_target_instance_edit_url()

    assert redirect_url is not None
    assertRedirects(post_response, redirect_url)

    job = Job.objects.get(
        first_synced_at=None,
        last_synced_at=None,
        translation_source__object__content_type=ContentType.objects.get_for_model(
            Page
        ),
        translation_source__object__translation_key=page.translation_key,
    )

    job.status = status
    update_fields = ["status"]
    if job.status != JobStatus.UNSYNCED:
        now = timezone.now()
        job.first_synced_at = now
        job.last_synced_at = now
        job.translation_job_uid = "foo"
        update_fields += ["first_synced_at", "last_synced_at", "translation_job_uid"]

    job.save(update_fields=update_fields)

    response = client.get(redirect_url)
    expected = (
        "This translation is managed by Smartling. Changes made here will be "
        "lost the next time translations are imported from Smartling. "
        f"Job status: {job.get_status_display()}"  # pyright: ignore[reportAttributeAccessIssue]
    )
    if show_message:
        assert expected in response.content.decode("utf-8")
    else:
        assert expected not in response.content.decode("utf-8")


# TODO test existing jobs in various states
# TODO test child pages in various states of translation
