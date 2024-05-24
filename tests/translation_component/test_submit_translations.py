"""
Tests for the wagtail-localize translation components functionality when
submitting a page for translation
"""

import json

from urllib.parse import quote, urljoin

import pytest

from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from pytest_django.asserts import assertRedirects
from wagtail.models import Locale, Page
from wagtail_localize.models import (
    Translation,
    TranslationSource,
    get_edit_url,
)
from wagtail_localize_smartling.api.types import (
    GetProjectDetailsResponseData,
    ListJobsResponseData,
    TargetLocaleData,
)
from wagtail_localize_smartling.models import Job

from tests.testapp.factories import InfoPageFactory
from tests.testapp.models import InfoPage


@pytest.mark.parametrize(
    ["target_locales"],
    [
        [("de",)],
        [("de", "fr")],
    ],
)
@pytest.mark.parametrize(
    ["name", "description", "due_date"],
    [
        ("Foo", "Bar", None),
    ],
)
@pytest.mark.django_db()
def test_no_existing_jobs_no_child_pages(
    client,
    description,
    due_date,
    dummy_smartling_settings,
    name,
    responses,
    root_page,
    superuser,
    target_locales,
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

    # Mock response to indicate no existing jobs have the same name
    responses.get(
        f"https://api.smartling.com/jobs-api/v3/projects/{quote(dummy_smartling_settings['PROJECT_ID'])}/jobs?jobName={quote(name)}",
        body=json.dumps(
            {
                "response": {
                    "code": "SUCCESS",
                    "data": ListJobsResponseData(items=[]),
                }
            }
        ),
    )

    get_response = client.get(submit_url)
    assert get_response.status_code == 200

    component_mgr = get_response.context["components"]
    assert len(component_mgr.components) == 1

    component_form = component_mgr.components[0][2]
    assert component_form.fields["name"].initial == "Component test page"
    assert component_form.fields["description"].initial == (
        "Automatically-created Wagtail translation job for "
        f'"{page}": {urljoin("http://testserver/", page_edit_url)}'
    )

    post_response = client.post(
        submit_url,
        data={
            "locales": target_locale_ids,
            f"{component_form_prefix}-enabled": name,
            f"{component_form_prefix}-name": name,
            f"{component_form_prefix}-description": description,
            f"{component_form_prefix}-due_date": due_date or "",
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
    assert job.name == name
    assert job.description == description
    assert job.due_date == due_date
    assert job.reference_number == f"translationsource_id:{job.translation_source.pk}"
