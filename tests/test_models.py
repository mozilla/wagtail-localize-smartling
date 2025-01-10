import pytest

from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.utils import timezone
from freezegun import freeze_time
from wagtail.models import Locale
from wagtail_localize.models import Translation, TranslationSource
from wagtail_localize.operations import translate_object

from wagtail_localize_smartling.models import Job, LandedTranslationTask
from wagtail_localize_smartling.utils import get_snippet_admin_url

from testapp.factories import InfoPageFactory, InfoSnippetFactory
from testapp.settings import job_description_callback


pytestmark = pytest.mark.django_db


def test_default_job_description(smartling_job: "Job"):
    page = smartling_job.translation_source.get_source_instance()
    assert smartling_job.description == f"CMS translation job for info page '{page}'."


def test_job_description_callback(smartling_job: "Job", smartling_settings):
    smartling_settings.JOB_DESCRIPTION_CALLBACK = job_description_callback
    description = smartling_job.get_description(
        smartling_job.translation_source, smartling_job.translations.all()
    )
    assert description == "1337"


@pytest.mark.parametrize("name_prefix", (None, "Test prefix 123"))
@freeze_time("2024-05-03 12:34:56.123456")
def test_Job_get_default_name(name_prefix, smartling_settings, root_page):
    page = InfoPageFactory(parent=root_page, title="Component test page")
    translation_source, _ = TranslationSource.get_or_create_from_instance(page)
    page_translation = Translation.objects.create(
        source=translation_source,
        target_locale=Locale.objects.get(language_code="fr"),
    )

    smartling_settings.JOB_NAME_PREFIX = name_prefix

    name = Job.get_default_name(translation_source, [page_translation])

    expected_name = f"70018f7c #{translation_source.pk}"

    if name_prefix:
        expected_name = f"{name_prefix} {expected_name}"

    assert expected_name == name


def test_LandedTranslationTaskManager_incomplete():
    locale, _ = Locale.objects.get_or_create(language_code="fr")
    content_type = ContentType.objects.get_for_model(InfoPageFactory())
    task1 = LandedTranslationTask.objects.create(
        content_type=content_type,
        object_id=1,
        relevant_locale=locale,
        completed_on=None,
        cancelled_on=None,
    )
    task2 = LandedTranslationTask.objects.create(
        content_type=content_type,
        object_id=2,
        relevant_locale=locale,
        completed_on=timezone.now(),
        cancelled_on=None,
    )
    task3 = LandedTranslationTask.objects.create(
        content_type=content_type,
        object_id=3,
        relevant_locale=locale,
        completed_on=None,
        cancelled_on=timezone.now(),
    )

    incomplete_tasks = LandedTranslationTask.objects.incomplete()  # pyright: ignore[reportAttributeAccessIssue]

    assert task1 in incomplete_tasks
    assert task2 not in incomplete_tasks
    assert task3 not in incomplete_tasks


def test_LandedTranslationTaskManager_create_from_source_and_translation(
    root_page,
):
    # Make some extra pages and locales, to ensure we just don't get a reset
    # autoid of 1 making all the tests pass

    Locale.objects.get_or_create(language_code="de")
    locale_fr, _ = Locale.objects.get_or_create(language_code="fr")
    InfoPageFactory(
        parent=root_page,
        title="Test page 1",
        slug="test-page-1",
    )
    page_2 = InfoPageFactory(
        parent=root_page,
        title="Test page 2",
        slug="test-page-2",
    )

    # bootstrap make a translation to mimic the work done by the view
    translate_object(
        instance=page_2,
        locales=[locale_fr],
    )
    page_translation = page_2.get_translations().get(
        locale=locale_fr,
    )

    task = LandedTranslationTask.objects.create_from_source_and_translation(  # pyright: ignore[reportAttributeAccessIssue]
        source_object=page_2,
        translated_locale=locale_fr,
    )

    assert task.object_id == page_translation.id
    assert task.content_type == ContentType.objects.get_for_model(page_2)
    assert task.content_object == page_translation
    assert task.relevant_locale == locale_fr
    assert task.completed_on is None
    assert task.cancelled_on is None


def test_LandedTranslationTask_edit_url_for_translated_item__page(root_page):
    locale_fr = Locale.objects.get(language_code="fr")
    page = InfoPageFactory(parent=root_page, title="Test page")
    translate_object(
        instance=page,
        locales=[locale_fr],
    )
    page_translation = page.get_translations().get(
        locale=locale_fr,
    )

    task = LandedTranslationTask.objects.create(
        content_type=ContentType.objects.get_for_model(page_translation),
        object_id=page_translation.pk,
        relevant_locale=locale_fr,
    )

    expected_url = reverse("wagtailadmin_pages:edit", args=[page_translation.pk])
    assert task.edit_url_for_translated_item() == expected_url


def test_LandedTranslationTask_edit_url_for_translated_item__snippet():
    snippet = InfoSnippetFactory(content="Test snippet")
    locale_fr = Locale.objects.get(language_code="fr")
    locale_de = Locale.objects.get(language_code="de")

    translate_object(
        instance=snippet,
        locales=[locale_de, locale_fr],
    )
    snippet_translation = snippet.get_translations().get(
        locale=locale_de,
    )

    task = LandedTranslationTask.objects.create(
        content_type=ContentType.objects.get_for_model(snippet_translation),
        object_id=snippet_translation.pk,
        relevant_locale=locale_de,
    )

    expected_url = get_snippet_admin_url(snippet_translation)
    assert task.edit_url_for_translated_item() == expected_url


def test_LandedTranslationTask_complete():
    locale, _ = Locale.objects.get_or_create(language_code="fr")
    content_type = ContentType.objects.get_for_model(InfoPageFactory())
    task = LandedTranslationTask.objects.create(
        content_type=content_type,
        object_id=1,
        relevant_locale=locale,
        completed_on=None,
        cancelled_on=None,
    )

    task.complete()

    assert task.is_completed()
    assert not task.is_cancelled()
    assert task.completed_on is not None
    assert task.cancelled_on is None


def test_LandedTranslationTask_cancel():
    locale, _ = Locale.objects.get_or_create(language_code="fr")
    content_type = ContentType.objects.get_for_model(InfoPageFactory())
    task = LandedTranslationTask.objects.create(
        content_type=content_type,
        object_id=1,
        relevant_locale=locale,
        completed_on=None,
        cancelled_on=None,
    )

    task.cancel()

    assert not task.is_completed()
    assert task.is_cancelled()
    assert task.completed_on is None
    assert task.cancelled_on is not None
