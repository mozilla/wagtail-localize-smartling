import pytest

from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.utils import timezone
from freezegun import freeze_time
from wagtail.models import Locale
from wagtail_localize.models import Translation, TranslationSource
from wagtail_localize.operations import translate_object

from wagtail_localize_smartling.api.types import JobStatus
from wagtail_localize_smartling.models import Job, JobTranslation, LandedTranslationTask
from wagtail_localize_smartling.utils import compute_content_hash, get_snippet_admin_url

from testapp.factories import InfoPageFactory, InfoSnippetFactory, UserFactory
from testapp.settings import job_description_callback


pytestmark = pytest.mark.django_db


def test_default_job_description(smartling_job: "Job"):
    page = smartling_job.translation_source.get_source_instance()
    assert smartling_job.description == f"CMS translation job for info page '{page}'."


def test_job_description_callback(smartling_job: "Job", smartling_settings):
    smartling_settings.JOB_DESCRIPTION_CALLBACK = job_description_callback
    description = smartling_job.get_description(smartling_job.translation_source, smartling_job.translations.all())
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


# =============================================================================
# JobTranslation model tests
# =============================================================================


def test_JobTranslation_created_with_job(smartling_job: Job):
    """Test that JobTranslation records are created when translations are added to a job."""
    job_translations = JobTranslation.objects.filter(job=smartling_job)
    assert job_translations.count() == 1

    job_translation = job_translations.first()
    assert job_translation.job == smartling_job
    assert job_translation.translation in smartling_job.translations.all()
    assert job_translation.imported_at is None


def test_JobTranslation_str(smartling_job: Job):
    """Test JobTranslation string representation."""
    job_translation = JobTranslation.objects.filter(job=smartling_job).first()
    expected = f"JobTranslation({smartling_job.pk}, {job_translation.translation_id})"
    assert str(job_translation) == expected


def test_JobTranslation_imported_at(smartling_job: Job):
    """Test that imported_at can be set on JobTranslation."""
    job_translation = JobTranslation.objects.filter(job=smartling_job).first()
    now = timezone.now()

    job_translation.imported_at = now
    job_translation.save()

    job_translation.refresh_from_db()
    assert job_translation.imported_at == now


# =============================================================================
# Issue #28: Adding locales to existing jobs
# =============================================================================


def test_get_or_create_adds_locales_to_unsynced_job(smartling_project, root_page):
    """Test that new locales are added to an existing UNSYNCED job."""
    user = UserFactory()
    page = InfoPageFactory(parent=root_page, title="Test page")
    translation_source, _ = TranslationSource.get_or_create_from_instance(page)
    content_hash = compute_content_hash(translation_source.export_po())

    locale_fr = Locale.objects.get(language_code="fr")
    locale_de = Locale.objects.get(language_code="de")

    # Create initial translation for fr
    fr_translation = Translation.objects.create(
        source=translation_source,
        target_locale=locale_fr,
    )

    # Create an UNSYNCED job with fr locale
    job = Job.objects.create(
        project=smartling_project,
        translation_source=translation_source,
        user=user,
        name="Test job",
        description="Test",
        reference_number="test",
        content_hash=content_hash,
        status=JobStatus.UNSYNCED,
    )
    job.translations.set([fr_translation])

    # Now try to add de locale
    de_translation = Translation.objects.create(
        source=translation_source,
        target_locale=locale_de,
    )

    Job.get_or_create_from_source_and_translation_data(
        translation_source=translation_source,
        translations=[de_translation],
        user=user,
        due_date=None,
    )

    # Should have added to existing job, not created a new one
    assert Job.objects.count() == 1
    job.refresh_from_db()
    assert job.translations.count() == 2
    assert set(job.translations.all()) == {fr_translation, de_translation}


def test_get_or_create_adds_locales_to_draft_job(smartling_project, root_page, smartling_add_locale_to_job):
    """Test that new locales are added to an existing DRAFT job via API."""
    user = UserFactory()
    page = InfoPageFactory(parent=root_page, title="Test page")
    translation_source, _ = TranslationSource.get_or_create_from_instance(page)
    content_hash = compute_content_hash(translation_source.export_po())

    locale_fr = Locale.objects.get(language_code="fr")
    locale_de = Locale.objects.get(language_code="de")

    # Create initial translation for fr
    fr_translation = Translation.objects.create(
        source=translation_source,
        target_locale=locale_fr,
    )

    # Create a DRAFT job (synced) with fr locale
    now = timezone.now()
    job = Job.objects.create(
        project=smartling_project,
        translation_source=translation_source,
        user=user,
        name="Test job",
        description="Test",
        reference_number="test",
        content_hash=content_hash,
        status=JobStatus.DRAFT,
        translation_job_uid="test_job_uid",
        first_synced_at=now,
        last_synced_at=now,
    )
    job.translations.set([fr_translation])

    # Mock the API call
    smartling_add_locale_to_job("test_job_uid", "de")

    # Now try to add de locale
    de_translation = Translation.objects.create(
        source=translation_source,
        target_locale=locale_de,
    )

    Job.get_or_create_from_source_and_translation_data(
        translation_source=translation_source,
        translations=[de_translation],
        user=user,
        due_date=None,
    )

    # Should have added to existing job, not created a new one
    assert Job.objects.count() == 1
    job.refresh_from_db()
    assert job.translations.count() == 2


def test_get_or_create_creates_new_job_for_in_progress(smartling_project, root_page):
    """Test that a new job is created when existing job is IN_PROGRESS."""
    user = UserFactory()
    page = InfoPageFactory(parent=root_page, title="Test page")
    translation_source, _ = TranslationSource.get_or_create_from_instance(page)
    content_hash = compute_content_hash(translation_source.export_po())

    locale_fr = Locale.objects.get(language_code="fr")
    locale_de = Locale.objects.get(language_code="de")

    # Create initial translation for fr
    fr_translation = Translation.objects.create(
        source=translation_source,
        target_locale=locale_fr,
    )

    # Create an IN_PROGRESS job with fr locale
    now = timezone.now()
    job = Job.objects.create(
        project=smartling_project,
        translation_source=translation_source,
        user=user,
        name="Test job",
        description="Test",
        reference_number="test",
        content_hash=content_hash,
        status=JobStatus.IN_PROGRESS,
        translation_job_uid="in_progress_job_uid",
        first_synced_at=now,
        last_synced_at=now,
    )
    job.translations.set([fr_translation])

    # Now try to add de locale
    de_translation = Translation.objects.create(
        source=translation_source,
        target_locale=locale_de,
    )

    Job.get_or_create_from_source_and_translation_data(
        translation_source=translation_source,
        translations=[de_translation],
        user=user,
        due_date=None,
    )

    # Should have created a new job
    assert Job.objects.count() == 2
    new_job = Job.objects.exclude(pk=job.pk).first()
    assert new_job.translations.count() == 1
    assert de_translation in new_job.translations.all()


def test_get_or_create_no_action_when_all_locales_covered(smartling_project, root_page):
    """Test that no job is created when all locales are already covered."""
    user = UserFactory()
    page = InfoPageFactory(parent=root_page, title="Test page")
    translation_source, _ = TranslationSource.get_or_create_from_instance(page)
    content_hash = compute_content_hash(translation_source.export_po())

    locale_fr = Locale.objects.get(language_code="fr")

    # Create initial translation for fr
    fr_translation = Translation.objects.create(
        source=translation_source,
        target_locale=locale_fr,
    )

    # Create an UNSYNCED job with fr locale
    job = Job.objects.create(
        project=smartling_project,
        translation_source=translation_source,
        user=user,
        name="Test job",
        description="Test",
        reference_number="test",
        content_hash=content_hash,
        status=JobStatus.UNSYNCED,
    )
    job.translations.set([fr_translation])

    # Try to submit the same locale again
    Job.get_or_create_from_source_and_translation_data(
        translation_source=translation_source,
        translations=[fr_translation],
        user=user,
        due_date=None,
    )

    # Should not have created a new job
    assert Job.objects.count() == 1


# =============================================================================
# EXCLUDE_LOCALES filtering tests
# =============================================================================


def test_get_or_create_excludes_locales(smartling_project, smartling_settings, root_page):
    """Translations for excluded locales are not added to Smartling jobs."""
    smartling_settings.EXCLUDE_LOCALES = frozenset(["de"])

    user = UserFactory()
    page = InfoPageFactory(parent=root_page, title="Test page")
    translation_source, _ = TranslationSource.get_or_create_from_instance(page)

    locale_fr = Locale.objects.get(language_code="fr")
    locale_de = Locale.objects.get(language_code="de")

    fr_translation = Translation.objects.create(
        source=translation_source,
        target_locale=locale_fr,
    )
    de_translation = Translation.objects.create(
        source=translation_source,
        target_locale=locale_de,
    )

    Job.get_or_create_from_source_and_translation_data(
        translation_source=translation_source,
        translations=[fr_translation, de_translation],
        user=user,
        due_date=None,
    )

    assert Job.objects.count() == 1
    job = Job.objects.first()
    assert set(job.translations.all()) == {fr_translation}


def test_get_or_create_all_excluded_no_job(smartling_project, smartling_settings, root_page):
    """No job is created when all translations target excluded locales."""
    smartling_settings.EXCLUDE_LOCALES = frozenset(["fr", "de"])

    user = UserFactory()
    page = InfoPageFactory(parent=root_page, title="Test page")
    translation_source, _ = TranslationSource.get_or_create_from_instance(page)

    fr_translation = Translation.objects.create(
        source=translation_source,
        target_locale=Locale.objects.get(language_code="fr"),
    )
    de_translation = Translation.objects.create(
        source=translation_source,
        target_locale=Locale.objects.get(language_code="de"),
    )

    Job.get_or_create_from_source_and_translation_data(
        translation_source=translation_source,
        translations=[fr_translation, de_translation],
        user=user,
        due_date=None,
    )

    assert Job.objects.count() == 0


def test_get_or_create_excluded_locale_not_added_to_existing_job(smartling_project, smartling_settings, root_page):
    """Excluded locales are filtered out, and already-covered locales don't create a new job."""
    smartling_settings.EXCLUDE_LOCALES = frozenset(["de"])

    user = UserFactory()
    page = InfoPageFactory(parent=root_page, title="Test page")
    translation_source, _ = TranslationSource.get_or_create_from_instance(page)
    content_hash = compute_content_hash(translation_source.export_po())

    locale_fr = Locale.objects.get(language_code="fr")
    locale_de = Locale.objects.get(language_code="de")

    fr_translation = Translation.objects.create(
        source=translation_source,
        target_locale=locale_fr,
    )

    # Create an existing UNSYNCED job with fr
    job = Job.objects.create(
        project=smartling_project,
        translation_source=translation_source,
        user=user,
        name="Test job",
        description="Test",
        reference_number="test",
        content_hash=content_hash,
        status=JobStatus.UNSYNCED,
    )
    job.translations.set([fr_translation])

    # Submit both fr (already covered) and de (excluded)
    de_translation = Translation.objects.create(
        source=translation_source,
        target_locale=locale_de,
    )

    Job.get_or_create_from_source_and_translation_data(
        translation_source=translation_source,
        translations=[fr_translation, de_translation],
        user=user,
        due_date=None,
    )

    # No new job, existing job unchanged
    assert Job.objects.count() == 1
    assert job.translations.count() == 1
    assert set(job.translations.all()) == {fr_translation}
