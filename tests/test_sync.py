"""Tests for the sync module, including per-locale import (Issue #37)."""

import json

from datetime import timedelta
from unittest.mock import patch
from urllib.parse import quote

import pytest

from django.db import DatabaseError
from django.utils import timezone

from wagtail_localize_smartling.api.types import JobStatus
from wagtail_localize_smartling.models import Job, JobTranslation
from wagtail_localize_smartling.sync import (
    SYNC_LEASE_SECONDS,
    SyncJobException,
    _check_and_import_completed_locales,
    _compute_translation_hash,
    _import_translation_for_locale,
    _push_is_incomplete,
    _release_claim,
    _sanitize_po_content,
    _sync,
    sync_job,
)

from tests.conftest import CREATE_JOB_RESPONSE_BODY
from tests.factories import JobFactory


pytestmark = pytest.mark.django_db

# What the smartling_create_batch_for_job fixture hands back, which is not the
# batch the smartling_upload_files_to_job_batch fixture stubs.
BATCH_UID = "dummy_batch_uid"


def register_file_upload_to_batch(responses, settings, *, succeeds: bool) -> None:
    """Register one batch-file upload response; `responses` serves registrations in order."""
    project_id = settings.WAGTAIL_LOCALIZE_SMARTLING["PROJECT_ID"]
    if succeeds:
        body = {"response": {"code": "ACCEPTED", "data": {}}}
        status = 202
    else:
        body = {
            "response": {
                "code": "VALIDATION_ERROR",
                "data": [{"key": "some upload key", "message": "some upload message", "details": "some details"}],
            }
        }
        status = 400

    responses.add(
        method="POST",
        url=f"https://api.smartling.com/job-batches-api/v2/projects/{quote(project_id)}/batches/{BATCH_UID}/file",
        body=json.dumps(body),
        status=status,
    )


@pytest.fixture
def disable_signals():
    """Disable translation import signals during tests."""
    with patch("wagtail_localize_smartling.sync.individual_translation_imported") as mock_signal:
        mock_signal.send = lambda *args, **kwargs: None
        yield mock_signal


class TestCheckAndImportCompletedLocales:
    """Tests for _check_and_import_completed_locales function (Issue #37)."""

    def test_imports_completed_locale(
        self,
        smartling_job_multi_locale: Job,
        smartling_get_file_status,
        smartling_download_translation_for_locale,
        disable_signals,
    ):
        """Test that a completed locale is imported while job is in progress."""
        # Set up: fr is complete, de is not
        smartling_get_file_status("fr", total_strings=10, completed_strings=10)
        smartling_get_file_status("de", total_strings=10, completed_strings=5)
        smartling_download_translation_for_locale("fr")

        # Verify job_translations exist and are not imported
        job_translations = JobTranslation.objects.filter(job=smartling_job_multi_locale)
        assert job_translations.count() == 2
        assert all(jt.imported_at is None for jt in job_translations)

        # Run the import
        imported = _check_and_import_completed_locales(smartling_job_multi_locale)

        # Should have imported fr but not de
        assert len(imported) == 1
        assert imported[0].target_locale.language_code == "fr"

        # Check JobTranslation records
        fr_jt = job_translations.get(translation__target_locale__language_code="fr")
        de_jt = job_translations.get(translation__target_locale__language_code="de")

        assert fr_jt.imported_at is not None
        assert de_jt.imported_at is None

    def test_no_import_when_not_complete(
        self,
        smartling_job_multi_locale: Job,
        smartling_get_file_status,
    ):
        """Test that incomplete locales are not imported."""
        # Both locales incomplete
        smartling_get_file_status("fr", total_strings=10, completed_strings=5)
        smartling_get_file_status("de", total_strings=10, completed_strings=3)

        imported = _check_and_import_completed_locales(smartling_job_multi_locale)

        assert len(imported) == 0

        # No JobTranslations should be marked as imported
        job_translations = JobTranslation.objects.filter(job=smartling_job_multi_locale)
        assert all(jt.imported_at is None for jt in job_translations)

    def test_skips_already_imported(
        self,
        smartling_job_multi_locale: Job,
        smartling_get_file_status,
    ):
        """Test that already imported locales are skipped."""
        # Mark fr as already imported
        fr_jt = JobTranslation.objects.get(
            job=smartling_job_multi_locale,
            translation__target_locale__language_code="fr",
        )
        fr_jt.imported_at = timezone.now()
        fr_jt.save()

        # Only de should be checked (and it's incomplete)
        smartling_get_file_status("de", total_strings=10, completed_strings=5)

        imported = _check_and_import_completed_locales(smartling_job_multi_locale)

        assert len(imported) == 0

    def test_imports_all_completed_locales(
        self,
        smartling_job_multi_locale: Job,
        smartling_get_file_status,
        smartling_download_translation_for_locale,
        disable_signals,
    ):
        """Test that all completed locales are imported."""
        # Both locales complete
        smartling_get_file_status("fr", total_strings=10, completed_strings=10)
        smartling_get_file_status("de", total_strings=10, completed_strings=10)
        smartling_download_translation_for_locale("fr")
        smartling_download_translation_for_locale("de")

        imported = _check_and_import_completed_locales(smartling_job_multi_locale)

        assert len(imported) == 2

        # Both should be marked as imported
        job_translations = JobTranslation.objects.filter(job=smartling_job_multi_locale)
        assert all(jt.imported_at is not None for jt in job_translations)

    def test_handles_api_error_gracefully(
        self,
        smartling_job_multi_locale: Job,
        smartling_get_file_status,
        smartling_download_translation_for_locale,
        responses,
        disable_signals,
    ):
        """Test that API errors for one locale don't prevent others from importing."""
        # fr will succeed, de will fail
        smartling_get_file_status("fr", total_strings=10, completed_strings=10)
        smartling_download_translation_for_locale("fr")
        # de request not mocked, so it will fail

        # Should still import fr
        imported = _check_and_import_completed_locales(smartling_job_multi_locale)

        assert len(imported) == 1
        assert imported[0].target_locale.language_code == "fr"


class TestImportTranslationForLocale:
    """Tests for _import_translation_for_locale function."""

    def test_imports_single_locale(
        self,
        smartling_job: Job,
        smartling_download_translation_for_locale,
        disable_signals,
    ):
        """Test importing a single locale's translation."""
        smartling_download_translation_for_locale("fr")

        translation = smartling_job.translations.first()
        _import_translation_for_locale(smartling_job, translation, "fr")

        # The translation should have been imported (import_po was called)
        # We can verify by checking that no exception was raised


class TestSanitizePoContent:
    """Tests for _sanitize_po_content."""

    def test_strips_unicode_line_separator(self):
        content = 'msgstr "Take control \u2028of AI"'
        assert _sanitize_po_content(content) == 'msgstr "Take control  of AI"'

    def test_strips_unicode_paragraph_separator(self):
        content = 'msgstr "Hello\u2029World"'
        assert _sanitize_po_content(content) == 'msgstr "Hello World"'

    def test_clean_content_unchanged(self):
        content = 'msgstr "Hello World"'
        assert _sanitize_po_content(content) == content


class TestComputeTranslationHashWithUnicodeSeparators:
    """Test that _compute_translation_hash handles U+2028/U+2029 in PO content."""

    def test_hash_succeeds_with_unicode_line_separator_in_msgstr(self):
        po_content = """\
msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"

msgctxt "title"
msgid ""
"Take control \u2028"
"of AI"
msgstr "Kontrolle \u2028über KI"
"""
        # Should not raise
        result = _compute_translation_hash(po_content)
        assert isinstance(result, str)
        assert len(result) == 64  # SHA-256 hex digest


class TestImportTranslationForLocaleWithUnicodeSeparators:
    """Test that _import_translation_for_locale handles U+2028 in PO content."""

    pytestmark = pytest.mark.django_db

    def test_imports_with_unicode_line_separator_in_msgstr(
        self,
        smartling_job,
        smartling_download_translation_for_locale,
        disable_signals,
    ):
        po_content = """\
msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"

msgctxt "title"
msgid ""
"Take control \u2028"
"of AI"
msgstr "Kontrolle \u2028über KI"
"""
        smartling_download_translation_for_locale("fr", po_content=po_content)
        translation = smartling_job.translations.first()
        # Should not raise
        _import_translation_for_locale(smartling_job, translation, "fr")


class TestPushIsIncomplete:
    def test_a_job_that_was_never_pushed_is_incomplete(self, root_page):
        assert _push_is_incomplete(JobFactory(source_instance=root_page, unsynced=True))

    def test_a_job_whose_upload_never_landed_is_incomplete(self, root_page):
        job = JobFactory(source_instance=root_page, synced=True)
        assert _push_is_incomplete(job)

    def test_a_job_with_every_file_uploaded_is_complete(self, root_page):
        job = JobFactory(source_instance=root_page, synced=True)
        job.files.update(file_uri="job_1_ts_1.po")

        assert not _push_is_incomplete(job)


class TestSyncWriteOrder:
    def test_a_failed_import_leaves_the_status_for_the_next_sweep(self, root_page):
        """Nothing rolls a premature status write back, and an unchanged status is never retried."""
        job = JobFactory(source_instance=root_page, synced=True)
        job_details = {
            "jobStatus": JobStatus.COMPLETED,
            "description": "",
            "referenceNumber": "",
            "dueDate": None,
        }

        with (
            patch("wagtail_localize_smartling.sync.client.get_job_details", return_value=job_details),
            patch("wagtail_localize_smartling.sync._download_and_apply_translations", side_effect=OSError("boom")),
            pytest.raises(OSError),
        ):
            _sync(job)

        job.refresh_from_db()
        assert job.status == JobStatus.AWAITING_AUTHORIZATION


class TestSyncJobClaim:
    def test_a_failed_push_keeps_the_smartling_job_it_created(
        self, root_page, smartling_create_job, smartling_create_batch_for_job, responses, settings
    ):
        """Smartling keeps the job the push created, so rolling the row back would orphan it."""
        job = JobFactory(source_instance=root_page, unsynced=True)
        register_file_upload_to_batch(responses, settings, succeeds=False)

        # Currently, the job does not have a translation_job_uid, since it has
        # not been synced.
        assert job.translation_job_uid == ""

        with pytest.raises(SyncJobException):
            sync_job(job.pk)

        job.refresh_from_db()
        assert job.translation_job_uid == "dummy_translation_job_uid"
        assert job.sync_started_at is None

    def test_a_successful_push_releases_its_claim(
        self, root_page, smartling_create_job, smartling_create_batch_for_job, responses, settings
    ):
        """
        Releasing the claim means clearing sync_started_at.

        Every other worker skips a job whose claim is still fresh, so a push
        that finished without clearing it would leave the job untouchable
        until the lease ran out.
        """
        job = JobFactory(source_instance=root_page, unsynced=True)
        register_file_upload_to_batch(responses, settings, succeeds=True)

        sync_job(job.pk)

        job.refresh_from_db()
        job_file = job.files.get()
        assert job.sync_started_at is None
        assert job_file.file_uri == f"job_{job.pk}_ts_{job_file.translation_source_id}.po"
        assert job.file_uri == job_file.file_uri

    def test_the_file_uri_lands_on_the_job_and_on_the_file_together(
        self, root_page, smartling_create_job, smartling_create_batch_for_job, responses, settings
    ):
        """The file_uri gets saved on the Job and JobFile, or neither."""
        job = JobFactory(source_instance=root_page, unsynced=True)
        register_file_upload_to_batch(responses, settings, succeeds=True)
        save_the_job = Job.save

        def fail_when_the_job_records_its_uri(self, *args, **kwargs):
            if kwargs.get("update_fields") == ["file_uri"]:
                raise OSError("boom")
            return save_the_job(self, *args, **kwargs)

        with patch.object(Job, "save", fail_when_the_job_records_its_uri), pytest.raises(SyncJobException):
            sync_job(job.pk)

        job.refresh_from_db()
        assert job.file_uri == ""
        assert job.files.get().file_uri == ""

    def test_a_job_already_being_pushed_is_left_alone(self, root_page, responses):
        claimed_at = timezone.now()
        job = JobFactory(source_instance=root_page, unsynced=True, sync_started_at=claimed_at)

        sync_job(job.pk)

        job.refresh_from_db()
        assert [c.request.url for c in responses.calls] == []
        assert job.sync_started_at == claimed_at

    def test_releasing_a_claim_leaves_a_takeover_alone(self, root_page):
        """The slow worker finishing after a takeover must not clear the new worker's claim."""
        taken_over_at = timezone.now()
        job = JobFactory(
            source_instance=root_page,
            unsynced=True,
            sync_started_at=taken_over_at - timedelta(seconds=SYNC_LEASE_SECONDS + 1),
        )
        claimed_at = job.sync_started_at
        Job.objects.filter(pk=job.pk).update(sync_started_at=taken_over_at)

        _release_claim(job, claimed_at=claimed_at)

        job.refresh_from_db()
        assert job.sync_started_at == taken_over_at

    def test_a_push_does_not_stamp_its_own_claim_back_over_a_takeover(
        self, root_page, smartling_create_batch_for_job, responses, settings
    ):
        """Saving the whole job writes back the claim this worker read before it started pushing."""
        job = JobFactory(source_instance=root_page, unsynced=True)
        taken_over_at = timezone.now()
        project_id = settings.WAGTAIL_LOCALIZE_SMARTLING["PROJECT_ID"]

        def take_the_job_over(request):
            Job.objects.filter(pk=job.pk).update(sync_started_at=taken_over_at)
            return 200, {}, json.dumps(CREATE_JOB_RESPONSE_BODY)

        responses.add_callback(
            method="POST",
            url=f"https://api.smartling.com/jobs-api/v3/projects/{quote(project_id)}/jobs",
            callback=take_the_job_over,
        )
        register_file_upload_to_batch(responses, settings, succeeds=True)

        sync_job(job.pk)

        job.refresh_from_db()
        assert job.sync_started_at == taken_over_at

    def test_a_poll_does_not_stamp_its_own_claim_back_over_a_takeover(self, root_page):
        job = JobFactory(source_instance=root_page, synced=True)
        job.files.update(file_uri="job_1_ts_1.po")
        taken_over_at = timezone.now()

        def take_the_job_over(**kwargs):
            Job.objects.filter(pk=job.pk).update(sync_started_at=taken_over_at)
            return {
                "jobStatus": JobStatus.AWAITING_AUTHORIZATION,
                "description": "",
                "referenceNumber": "",
                "dueDate": None,
            }

        with patch("wagtail_localize_smartling.sync.client.get_job_details", side_effect=take_the_job_over):
            sync_job(job.pk)

        job.refresh_from_db()
        assert job.sync_started_at == taken_over_at

    def test_a_claim_that_cannot_be_released_does_not_fail_the_sync(
        self, root_page, smartling_create_job, smartling_create_batch_for_job, responses, settings
    ):
        job = JobFactory(source_instance=root_page, unsynced=True)
        register_file_upload_to_batch(responses, settings, succeeds=True)

        with patch("wagtail_localize_smartling.sync._release_claim", side_effect=DatabaseError("boom")):
            sync_job(job.pk)

        job.refresh_from_db()
        assert job.translation_job_uid == "dummy_translation_job_uid"

    def test_a_claim_older_than_the_lease_is_taken_over(
        self, root_page, smartling_create_job, smartling_create_batch_for_job, responses, settings
    ):
        job = JobFactory(
            source_instance=root_page,
            unsynced=True,
            sync_started_at=timezone.now() - timedelta(seconds=SYNC_LEASE_SECONDS + 1),
        )
        register_file_upload_to_batch(responses, settings, succeeds=True)

        sync_job(job.pk)

        job.refresh_from_db()
        assert job.translation_job_uid == "dummy_translation_job_uid"
