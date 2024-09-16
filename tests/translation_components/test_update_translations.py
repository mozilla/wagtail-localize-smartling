import pytest

from django.urls import reverse
from django.utils import timezone
from pytest_django.asserts import assertRedirects
from wagtail.models import Locale
from wagtail_localize.models import (
    Translation,
    TranslationSource,
)

from wagtail_localize_smartling.api.types import JobStatus
from wagtail_localize_smartling.models import Job

from testapp.factories import InfoPageFactory


pytestmark = pytest.mark.django_db


def test_update_translation_with_existing_pending_job_with_same_content_no_child_pages(
    client, root_page, superuser, smartling_project
):
    page = InfoPageFactory(parent=root_page, title="Component test page")
    target_locale_ids = Locale.objects.values_list("pk", flat=True).filter(
        language_code="de"
    )

    component_form_prefix = f"component-{Job._meta.db_table}"

    submit_translation_url = reverse(
        "wagtail_localize:submit_page_translation",
        kwargs={"page_id": page.pk},
    )
    client.force_login(superuser)

    response = client.get(submit_translation_url)
    assert response.status_code == 200

    component_mgr = response.context["components"]
    assert len(component_mgr.components) == 1

    post_response = client.post(
        submit_translation_url,
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
    assert {t.target_locale.language_code for t in translations} == {"de"}

    assert Job.objects.filter(translation_source=translation_source).count() == 1

    # Now submit translations
    update_translation_url = reverse(
        "wagtail_localize:update_translations",
        kwargs={"translation_source_id": translation_source.pk},
    )

    response = client.get(update_translation_url)
    assert response.status_code == 200

    component_mgr = response.context["components"]
    assert len(component_mgr.components) == 1

    post_response = client.post(
        update_translation_url,
        data={
            "locales": target_locale_ids,
            f"{component_form_prefix}-enabled": True,
            f"{component_form_prefix}-due_date": "",
        },
    )

    assertRedirects(
        post_response, reverse("wagtailadmin_explore", args=[page.get_parent().id])
    )
    assert Job.objects.filter(translation_source=translation_source).count() == 1


def test_update_translation_with_existing_completed_job_and_same_content_no_child_pages(
    client, root_page, superuser, smartling_project
):
    page = InfoPageFactory(parent=root_page, title="Component test page")
    target_locale_ids = Locale.objects.values_list("pk", flat=True).filter(
        language_code="de"
    )

    component_form_prefix = f"component-{Job._meta.db_table}"

    submit_translation_url = reverse(
        "wagtail_localize:submit_page_translation",
        kwargs={"page_id": page.pk},
    )
    client.force_login(superuser)

    response = client.get(submit_translation_url)
    assert response.status_code == 200

    component_mgr = response.context["components"]
    assert len(component_mgr.components) == 1

    client.post(
        submit_translation_url,
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
    assert {t.target_locale.language_code for t in translations} == {"de"}

    assert Job.objects.filter(translation_source=translation_source).count() == 1

    now = timezone.now()
    job: Job = Job.objects.filter(translation_source=translation_source).first()  # pyright: ignore[reportAssignmentType]
    job.status = JobStatus.COMPLETED
    job.first_synced_at = now
    job.last_synced_at = now
    job.translation_job_uid = "foo"

    job.save(
        update_fields=[
            "status",
            "first_synced_at",
            "last_synced_at",
            "translation_job_uid",
        ]
    )

    # Now submit translations
    update_translation_url = reverse(
        "wagtail_localize:update_translations",
        kwargs={"translation_source_id": translation_source.pk},
    )

    response = client.get(update_translation_url)
    assert response.status_code == 200

    component_mgr = response.context["components"]
    assert len(component_mgr.components) == 1

    post_response = client.post(
        update_translation_url,
        data={
            "locales": target_locale_ids,
            f"{component_form_prefix}-enabled": True,
            f"{component_form_prefix}-due_date": "",
        },
    )

    assertRedirects(
        post_response, reverse("wagtailadmin_explore", args=[page.get_parent().id])
    )
    assert Job.objects.filter(translation_source=translation_source).count() == 2


def test_update_translation_with_existing_pending_job_with_new_content_no_child_pages(
    client, root_page, superuser, smartling_project
):
    page = InfoPageFactory(parent=root_page, title="Component test page")
    target_locale_ids = Locale.objects.values_list("pk", flat=True).filter(
        language_code="de"
    )

    component_form_prefix = f"component-{Job._meta.db_table}"

    submit_translation_url = reverse(
        "wagtail_localize:submit_page_translation",
        kwargs={"page_id": page.pk},
    )
    client.force_login(superuser)

    response = client.get(submit_translation_url)
    assert response.status_code == 200

    component_mgr = response.context["components"]
    assert len(component_mgr.components) == 1

    client.post(
        submit_translation_url,
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
    assert {t.target_locale.language_code for t in translations} == {"de"}

    assert Job.objects.filter(translation_source=translation_source).count() == 1

    page.title += " new"
    page.save_revision().publish()

    # Now submit translations
    update_translation_url = reverse(
        "wagtail_localize:update_translations",
        kwargs={"translation_source_id": translation_source.pk},
    )

    response = client.get(update_translation_url)
    assert response.status_code == 200

    component_mgr = response.context["components"]
    assert len(component_mgr.components) == 1

    post_response = client.post(
        update_translation_url,
        data={
            "locales": target_locale_ids,
            f"{component_form_prefix}-enabled": True,
            f"{component_form_prefix}-due_date": "",
        },
    )

    assertRedirects(
        post_response, reverse("wagtailadmin_explore", args=[page.get_parent().id])
    )
    assert Job.objects.filter(translation_source=translation_source).count() == 2
