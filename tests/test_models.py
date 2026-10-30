from datetime import timedelta

import pytest

from django.contrib.contenttypes.models import ContentType
from django.db.utils import DatabaseError, IntegrityError
from django.urls import reverse
from django.utils import timezone
from freezegun import freeze_time
from wagtail.models import Locale
from wagtail_localize.models import Translation, TranslationSource
from wagtail_localize.operations import translate_object

from wagtail_localize_smartling import models as wls_models
from wagtail_localize_smartling.api.types import JobStatus
from wagtail_localize_smartling.models import Job, JobTranslation, LandedTranslationTask
from wagtail_localize_smartling.utils import compute_content_hash, get_snippet_admin_url

from testapp.factories import (
    InfoPageFactory,
    InfoSnippetFactory,
    TranslationSourceFactory,
    UserFactory,
)
from testapp.settings import job_description_callback
from tests.factories import JobFactory, OrderFactory, OrderItemFactory


pytestmark = pytest.mark.django_db


def target_locales():
    """Every locale the autouse `locales` fixture installs, source locale excluded."""
    return list(Locale.objects.exclude(pk=Locale.get_default().pk))


def _second_page(root_page):
    return InfoPageFactory(parent=root_page, title="Second page")


def test_default_job_description(smartling_job: "Job"):
    page = smartling_job.translation_source.get_source_instance()
    assert smartling_job.description == f"Order: {page}\n\n- info page '{page}'"


def test_job_description_callback(smartling_job: "Job", smartling_settings):
    smartling_settings.JOB_DESCRIPTION_CALLBACK = job_description_callback
    assert Job.get_description_for_order(smartling_job.order, job=smartling_job) == "1337"


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
    wls_models.JobFile.objects.create(job=job, translation_source=translation_source, content_hash=content_hash)
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
    wls_models.JobFile.objects.create(job=job, translation_source=translation_source, content_hash=content_hash)
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
    wls_models.JobFile.objects.create(job=job, translation_source=translation_source, content_hash=content_hash)
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
    wls_models.JobFile.objects.create(job=job, translation_source=translation_source, content_hash=content_hash)
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
    wls_models.JobFile.objects.create(job=job, translation_source=translation_source, content_hash=content_hash)
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


def test_order_starts_as_a_draft(superuser):
    order = wls_models.Order.objects.create(name="WNP 157", created_by=superuser)

    assert order.is_draft is True
    assert order.reference_number != ""


def test_order_reference_number_is_stable_across_saves(superuser):
    order = wls_models.Order.objects.create(name="WNP 157", created_by=superuser)
    original_reference_number = order.reference_number

    order.name = "WNP 158"
    order.save()
    order.refresh_from_db()

    assert order.reference_number == original_reference_number


def test_an_order_item_remembers_the_locales_it_was_added_for(superuser, root_page, locales):
    order = wls_models.Order.objects.create(name="WNP 157", created_by=superuser)
    source = TranslationSourceFactory(source_instance=root_page)
    french = Translation.objects.create(source=source, target_locale=Locale.objects.get(language_code="fr"))
    order_item = wls_models.OrderItem.objects.create(order=order, translation_source=source, added_by=superuser)
    order_item.translations.set([french])

    # A locale the editor did not tick. What was stored must not widen to match
    # what could be derived from the source.
    Translation.objects.create(source=source, target_locale=Locale.objects.get(language_code="de"))
    # The source page has translations in "fr" and "de" locales.
    assert set(source.translations.values_list("target_locale__language_code", flat=True)) == {"fr", "de"}

    assert list(order_item.translations.all()) == [french]


def test_a_page_cannot_be_added_to_the_same_order_twice(superuser, root_page):
    order = wls_models.Order.objects.create(name="WNP 157", created_by=superuser)
    source = TranslationSourceFactory(source_instance=root_page)
    wls_models.OrderItem.objects.create(order=order, translation_source=source, added_by=superuser)

    with pytest.raises(IntegrityError):
        wls_models.OrderItem.objects.create(order=order, translation_source=source, added_by=superuser)


def test_a_page_cannot_appear_twice_in_one_job(superuser, root_page):
    job = JobFactory(source_instance=root_page)
    source = job.files.get().translation_source

    with pytest.raises(IntegrityError):
        wls_models.JobFile.objects.create(job=job, translation_source=source)


def test_order_latest_job_is_the_most_recent_send_not_the_most_recently_synced(
    root_page, locales, superuser, smartling_project
):
    order = OrderFactory(created_by=superuser)
    failed = JobFactory(source_instance=root_page, order=order, unsynced=True)
    resent = JobFactory(source_instance=_second_page(root_page), order=order, synced=True)

    # A failed send never gets a first_synced_at, so Job's own ordering puts it first.
    assert order.jobs.first() == failed
    # The get_latest_job() method returns the Job that was created most recently.
    assert order.get_latest_job() == resent


def test_a_resend_is_suggested_a_distinct_job_name(superuser, root_page, smartling_settings):
    smartling_settings.JOB_NAME_PREFIX = "www.firefox.com"
    order = OrderFactory(name="WNP 157", created_by=superuser)

    assert order.suggested_job_name() == "www.firefox.com WNP 157"

    JobFactory(source_instance=root_page, order=order, synced=True)

    assert order.suggested_job_name() == "www.firefox.com WNP 157-2"


def test_a_job_is_not_left_behind_when_its_file_cannot_be_created(
    root_page, locales, superuser, smartling_project, mocker
):
    """
    If a Job's JobFile can not be created, then the Job is not created.

    Having a Job with no JobFile would result in sending the (invalid) job to
    Smartling, so we avoid creating it in the first place.
    """
    source = TranslationSourceFactory(source_instance=root_page)
    translations = [Translation.objects.create(source=source, target_locale=locale) for locale in target_locales()]
    mocker.patch.object(
        wls_models.JobFile.objects, "create", side_effect=DatabaseError("the file could not be written")
    )

    with pytest.raises(DatabaseError, match="the file could not be written"):
        wls_models.Job.get_or_create_from_source_and_translation_data(
            source, translations, user=superuser, due_date=None
        )

    assert wls_models.Job.objects.exists() is False


def test_creating_a_job_also_creates_its_file(root_page, locales, superuser, smartling_project):
    source = TranslationSourceFactory(source_instance=root_page)
    translations = [Translation.objects.create(source=source, target_locale=locale) for locale in target_locales()]

    assert wls_models.Job.objects.exists() is False

    wls_models.Job.get_or_create_from_source_and_translation_data(source, translations, user=superuser, due_date=None)

    job = wls_models.Job.objects.get()
    job_file = job.files.get()
    assert job_file.translation_source_id == source.pk
    assert job_file.content_hash == compute_content_hash(source.export_po())


def test_choosing_an_order_adds_an_item_and_creates_no_job(root_page, locales, superuser, smartling_project):
    order = OrderFactory(submitted_at=None, created_by=superuser)
    source = TranslationSourceFactory(source_instance=root_page)
    translations = [Translation.objects.create(source=source, target_locale=locale) for locale in target_locales()]

    wls_models.Job.get_or_create_from_source_and_translation_data(
        source, translations, user=superuser, due_date=None, order=order
    )

    assert wls_models.Job.objects.count() == 0
    item = order.order_items.get()
    assert item.translation_source_id == source.pk
    assert set(item.translations.all()) == set(translations)
    assert superuser in order.contributors.all()


def test_an_order_item_records_only_the_locales_that_survived_filtering(
    root_page, locales, superuser, smartling_project, smartling_settings
):
    """Excluded locales are filtered once, on the way in; nothing may re-derive them."""
    smartling_settings.EXCLUDE_LOCALES = frozenset(["de"])
    order = OrderFactory(submitted_at=None, created_by=superuser)
    source = TranslationSourceFactory(source_instance=root_page)
    french = Translation.objects.create(source=source, target_locale=Locale.objects.get(language_code="fr"))
    german = Translation.objects.create(source=source, target_locale=Locale.objects.get(language_code="de"))

    wls_models.Job.get_or_create_from_source_and_translation_data(
        source, [french, german], user=superuser, due_date=None, order=order
    )

    assert list(order.order_items.get().translations.all()) == [french]


def test_adding_the_same_page_to_an_order_twice_unions_its_locales(root_page, locales, superuser, smartling_project):
    order = OrderFactory(submitted_at=None, created_by=superuser)
    source = TranslationSourceFactory(source_instance=root_page)
    french = Translation.objects.create(source=source, target_locale=Locale.objects.get(language_code="fr"))
    german = Translation.objects.create(source=source, target_locale=Locale.objects.get(language_code="de"))

    wls_models.Job.get_or_create_from_source_and_translation_data(
        source, [french], user=superuser, due_date=None, order=order
    )
    wls_models.Job.get_or_create_from_source_and_translation_data(
        source, [german], user=superuser, due_date=None, order=order
    )

    assert order.order_items.count() == 1
    assert set(order.order_items.get().translations.all()) == {french, german}


def test_a_page_added_to_an_order_that_has_closed_is_sent_on_its_own(root_page, locales, superuser, smartling_project):
    completed_order = OrderFactory(submitted_at=timezone.now(), created_by=superuser)
    source = TranslationSourceFactory(source_instance=root_page)
    translations = [Translation.objects.create(source=source, target_locale=locale) for locale in target_locales()]

    wls_models.Job.get_or_create_from_source_and_translation_data(
        source, translations, user=superuser, due_date=None, order=completed_order
    )

    # Now there are 2 Orders.
    assert wls_models.Order.objects.count() == 2
    # The completed_order does not have any items.
    assert completed_order.order_items.count() == 0
    # There is a Job with its own Order.
    assert wls_models.Job.objects.count() == 1
    assert wls_models.Job.objects.get().order != completed_order


def test_a_send_now_job_gets_an_order_that_knows_its_page(root_page, locales, superuser, smartling_project):
    source = TranslationSourceFactory(source_instance=root_page)
    translations = [Translation.objects.create(source=source, target_locale=locale) for locale in target_locales()]

    wls_models.Job.get_or_create_from_source_and_translation_data(source, translations, user=superuser, due_date=None)

    order = wls_models.Job.objects.get().order
    assert order.is_draft is False
    assert [item.translation_source_id for item in order.order_items.all()] == [source.pk]
    assert set(order.order_items.get().translations.all()) == set(translations)


def test_send_now_still_reuses_a_pending_job_for_the_same_content(root_page, locales, superuser, smartling_project):
    source = TranslationSourceFactory(source_instance=root_page)
    first_locale, second_locale = target_locales()[:2]

    wls_models.Job.get_or_create_from_source_and_translation_data(
        source,
        [Translation.objects.create(source=source, target_locale=first_locale)],
        user=superuser,
        due_date=None,
    )
    wls_models.Job.get_or_create_from_source_and_translation_data(
        source,
        [Translation.objects.create(source=source, target_locale=second_locale)],
        user=superuser,
        due_date=None,
    )

    assert wls_models.Order.objects.count() == 1
    assert wls_models.Job.objects.count() == 1
    assert wls_models.Job.objects.get().translations.count() == 2


def test_submitting_an_order_creates_one_job_holding_every_page(root_page, locales, superuser, smartling_project):
    order = OrderFactory(submitted_at=None, created_by=superuser, name="WNP 157")
    source_a = TranslationSourceFactory(source_instance=root_page)
    source_b = TranslationSourceFactory(source_instance=_second_page(root_page))
    for source in (source_a, source_b):
        OrderItemFactory(
            order=order,
            translation_source=source,
            added_by=superuser,
            translations=[
                Translation.objects.create(source=source, target_locale=locale) for locale in target_locales()
            ],
        )

    job = order.submit(user=superuser)
    order.refresh_from_db()

    assert order.is_draft is False
    assert job.order == order
    # Hashed at submit, not when the page was added: the hash has to describe
    # what Smartling was actually sent.
    assert dict(job.files.values_list("translation_source_id", "content_hash")) == {
        source_a.pk: compute_content_hash(source_a.export_po()),
        source_b.pk: compute_content_hash(source_b.export_po()),
    }
    assert job.name.endswith("WNP 157")
    assert job.reference_number == order.reference_number


def test_submitting_sends_only_the_locales_each_page_was_ordered_in(root_page, locales, superuser, smartling_project):
    order = OrderFactory(submitted_at=None, created_by=superuser)
    source_a = TranslationSourceFactory(source_instance=root_page)
    source_b = TranslationSourceFactory(source_instance=_second_page(root_page))
    french_a = Translation.objects.create(source=source_a, target_locale=Locale.objects.get(language_code="fr"))
    # A locale the page has a translation for but was not ordered in.
    Translation.objects.create(source=source_a, target_locale=Locale.objects.get(language_code="de"))
    german_b = Translation.objects.create(source=source_b, target_locale=Locale.objects.get(language_code="de"))
    OrderItemFactory(order=order, translation_source=source_a, added_by=superuser, translations=[french_a])
    OrderItemFactory(order=order, translation_source=source_b, added_by=superuser, translations=[german_b])

    job = order.submit(user=superuser)

    assert set(job.translations.all()) == {french_a, german_b}


def test_submitting_drops_a_page_with_no_locales_left(root_page, locales, superuser, smartling_project):
    """A file with no authorizable strings makes the whole job unauthorizable."""
    order = OrderFactory(submitted_at=None, created_by=superuser)
    source_a = TranslationSourceFactory(source_instance=root_page)
    source_b = TranslationSourceFactory(source_instance=_second_page(root_page))
    french = Translation.objects.create(source=source_a, target_locale=Locale.objects.get(language_code="fr"))
    OrderItemFactory(order=order, translation_source=source_a, added_by=superuser, translations=[french])
    empty_item = OrderItemFactory(order=order, translation_source=source_b, added_by=superuser, translations=[])

    job = order.submit(user=superuser)

    assert list(job.files.values_list("translation_source_id", flat=True)) == [source_a.pk]
    assert order.dropped_order_items == [empty_item]


def test_submitting_an_order_with_nothing_left_to_send_is_refused(root_page, locales, superuser, smartling_project):
    """An empty job cannot be authorized, so it is better never created."""
    order = OrderFactory(submitted_at=None, created_by=superuser)
    source = TranslationSourceFactory(source_instance=root_page)
    OrderItemFactory(order=order, translation_source=source, added_by=superuser, translations=[])

    with pytest.raises(ValueError, match="no pages to translate"):
        order.submit(user=superuser)

    assert wls_models.Job.objects.exists() is False
    assert order.is_draft is True


@pytest.mark.parametrize(
    "due_offset,due_date_survives",
    [(timedelta(days=7), True), (timedelta(days=-1), False)],
    ids=["a due date still ahead is sent", "a due date already past is dropped"],
)
def test_a_due_date_that_has_already_passed_is_dropped_rather_than_sent(
    root_page, locales, superuser, smartling_project, due_offset, due_date_survives
):
    """An order sits for days before it is sent, and a resend is later still."""
    due_date = timezone.now() + due_offset
    order = OrderFactory(submitted_at=None, created_by=superuser, due_date=due_date)
    source = TranslationSourceFactory(source_instance=root_page)
    OrderItemFactory(
        order=order,
        translation_source=source,
        added_by=superuser,
        translations=[Translation.objects.create(source=source, target_locale=target_locales()[0])],
    )

    job = order.submit(user=superuser)

    if due_date_survives:
        assert job.due_date == due_date
        assert order.dropped_due_date is None
    else:
        assert job.due_date is None
        assert order.dropped_due_date == due_date
    # The pages are sent regardless of the due date, but if the due date has
    # already passed, they are sent with no due date.
    assert order.is_draft is False


def test_an_order_that_never_had_a_due_date_drops_nothing(root_page, locales, superuser, smartling_project):
    order = OrderFactory(submitted_at=None, created_by=superuser, due_date=None)
    source = TranslationSourceFactory(source_instance=root_page)
    OrderItemFactory(
        order=order,
        translation_source=source,
        added_by=superuser,
        translations=[Translation.objects.create(source=source, target_locale=target_locales()[0])],
    )

    job = order.submit(user=superuser)

    assert job.due_date is None
    assert order.dropped_due_date is None


def test_send_now_does_not_add_a_locale_to_a_job_that_holds_other_pages(
    root_page, locales, superuser, smartling_project
):
    source = TranslationSourceFactory(source_instance=root_page)
    other_source = TranslationSourceFactory(source_instance=_second_page(root_page))
    ordered_job = JobFactory(source_instance=root_page, files=[source, other_source], translations=[], unsynced=True)
    for job_file in wls_models.JobFile.objects.filter(job=ordered_job):
        job_file.content_hash = compute_content_hash(job_file.translation_source.export_po())
        job_file.save(update_fields=["content_hash"])

    wls_models.Job.get_or_create_from_source_and_translation_data(
        source,
        [Translation.objects.create(source=source, target_locale=target_locales()[0])],
        user=superuser,
        due_date=None,
    )

    assert ordered_job.translations.count() == 0
    assert wls_models.Job.objects.exclude(pk=ordered_job.pk).get().files.count() == 1


def test_a_page_edited_since_a_pending_job_was_made_is_sent_as_a_new_job(
    root_page, locales, superuser, smartling_project
):
    source = TranslationSourceFactory(source_instance=root_page)
    stale_job = JobFactory(source_instance=root_page, translations=[], unsynced=True)
    stale_job.files.update(content_hash="the page as it read yesterday")

    assert wls_models.Job.objects.exclude(pk=stale_job.pk).count() == 0

    wls_models.Job.get_or_create_from_source_and_translation_data(
        source,
        [Translation.objects.create(source=source, target_locale=target_locales()[0])],
        user=superuser,
        due_date=None,
    )

    assert stale_job.translations.count() == 0
    assert wls_models.Job.objects.exclude(pk=stale_job.pk).count() == 1


def test_a_resend_carries_only_the_pages_it_was_given(root_page, locales, superuser, smartling_project):
    """A resend covers what the failed send left behind, in the locales those pages were ordered in."""
    order = OrderFactory(submitted_at=None, created_by=superuser)
    source_a = TranslationSourceFactory(source_instance=root_page)
    source_b = TranslationSourceFactory(source_instance=_second_page(root_page))
    french_a = Translation.objects.create(source=source_a, target_locale=Locale.objects.get(language_code="fr"))
    german_b = Translation.objects.create(source=source_b, target_locale=Locale.objects.get(language_code="de"))
    item_a = OrderItemFactory(order=order, translation_source=source_a, added_by=superuser, translations=[french_a])
    OrderItemFactory(order=order, translation_source=source_b, added_by=superuser, translations=[german_b])

    job = order.submit(user=superuser, job_name="WNP 157-2", items=[item_a])

    assert job.name == "WNP 157-2"
    assert list(job.files.values_list("translation_source_id", flat=True)) == [source_a.pk]
    # The page left out takes its locale with it: a job carrying a locale it
    # has no file for cannot be authorized.
    assert set(job.translations.all()) == {french_a}
    assert german_b not in job.translations.all()


def test_a_resend_keeps_the_time_the_order_was_first_submitted(root_page, locales, superuser, smartling_project):
    order = OrderFactory(submitted_at=None, created_by=superuser)
    source = TranslationSourceFactory(source_instance=root_page)
    OrderItemFactory(
        order=order,
        translation_source=source,
        added_by=superuser,
        translations=[Translation.objects.create(source=source, target_locale=target_locales()[0])],
    )

    order.submit(user=superuser)
    first_submitted_at = wls_models.Order.objects.get(pk=order.pk).submitted_at

    order.submit(user=superuser)

    assert wls_models.Order.objects.get(pk=order.pk).submitted_at == first_submitted_at


def test_an_order_job_is_described_by_its_pages_its_notes_and_its_contributors(
    root_page, locales, superuser, smartling_project
):
    order = OrderFactory(submitted_at=None, created_by=superuser, name="WNP 157", notes="Ship with 157.")
    source = TranslationSourceFactory(source_instance=root_page)
    OrderItemFactory(
        order=order,
        translation_source=source,
        added_by=superuser,
        translations=[Translation.objects.create(source=source, target_locale=locale) for locale in target_locales()],
    )
    # Somebody other than the order's creator and the submitter, so that naming
    # either of those instead would not satisfy the assertion.
    assembler = UserFactory()
    order.contributors.add(assembler)

    job = order.submit(user=superuser)

    assert "WNP 157" in job.description
    assert "Ship with 157." in job.description
    assert assembler.get_username() in job.description
    assert str(source.get_source_instance()) in job.description


def test_a_partial_send_describes_only_the_pages_it_carries(root_page, locales, superuser, smartling_project):
    order = OrderFactory(submitted_at=None, created_by=superuser, name="WNP 157")
    source_a = TranslationSourceFactory(source_instance=root_page)
    source_b = TranslationSourceFactory(source_instance=_second_page(root_page))
    wanted = {}
    for source in (source_a, source_b):
        wanted[source] = [
            Translation.objects.create(source=source, target_locale=locale) for locale in target_locales()
        ]
        OrderItemFactory(order=order, translation_source=source, added_by=superuser, translations=wanted[source])

    job = order.submit(user=superuser, translations=wanted[source_a])

    assert order.order_items.count() == 2
    assert str(source_a.get_source_instance()) in job.description
    assert str(source_b.get_source_instance()) not in job.description


def test_a_send_now_job_still_describes_its_page(root_page, locales, superuser, smartling_project):
    page = InfoPageFactory(parent=root_page, title="Page sent on its own")
    source = TranslationSourceFactory(source_instance=page)
    translations = [Translation.objects.create(source=source, target_locale=locale) for locale in target_locales()]

    wls_models.Job.get_or_create_from_source_and_translation_data(source, translations, user=superuser, due_date=None)

    # The order of one is named after the page as well, so only the file line
    # tells us the job's pages were listed at all.
    assert f"- info page '{page}'" in wls_models.Job.objects.get().description


def test_the_description_callback_receives_the_job(
    root_page, locales, superuser, smartling_project, smartling_settings
):
    received = {}

    def callback(default_description, job):
        received["default_description"] = default_description
        received["job"] = job
        return "overridden"

    smartling_settings.JOB_DESCRIPTION_CALLBACK = callback
    order = OrderFactory(submitted_at=None, created_by=superuser, name="WNP 157")
    source = TranslationSourceFactory(source_instance=root_page)
    OrderItemFactory(
        order=order,
        translation_source=source,
        added_by=superuser,
        translations=[Translation.objects.create(source=source, target_locale=locale) for locale in target_locales()],
    )

    job = order.submit(user=superuser)

    assert received["job"] == job
    assert "WNP 157" in received["default_description"]
    # Read back rather than off the instance, which holds the callback's answer
    # either way: what needs pinning is that it reached the database.
    assert wls_models.Job.objects.get(pk=job.pk).description == "overridden"
