"""Tests for the sync module, including per-locale import (Issue #37)."""

import json

from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, quote, urlsplit

import pytest

from django.db import DatabaseError
from django.utils import timezone
from wagtail.models import Locale
from wagtail_localize.models import StringTranslation, Translation, TranslationSource

from wagtail_localize_smartling.api.client import FailedResponse, client
from wagtail_localize_smartling.api.client import JobNotFound as SmartlingJobNotFound
from wagtail_localize_smartling.api.types import JobStatus
from wagtail_localize_smartling.models import Job, JobTranslation
from wagtail_localize_smartling.sync import (
    SYNC_LEASE_SECONDS,
    FileURIMismatch,
    SyncJobException,
    _check_and_import_completed_locales,
    _compute_translation_hash,
    _download_and_apply_translations,
    _import_translation_for_locale,
    _initial_sync,
    _release_claim,
    _sanitize_po_content,
    _sync,
    is_failure_permanent,
    sync_job,
)

from testapp.factories import InfoPageFactory, TranslationSourceFactory
from tests.conftest import CREATE_JOB_RESPONSE_BODY
from tests.factories import JobFactory


pytestmark = pytest.mark.django_db

# What the smartling_create_batch_for_job fixture hands back, which is not the
# batch the smartling_upload_files_to_job_batch fixture stubs.
BATCH_UID = "dummy_batch_uid"
# A batch a job opened on an earlier sweep, distinct from BATCH_UID so that a
# test can tell a reused batch from a newly created one.
EARLIER_BATCH_UID = "an_earlier_batch_uid"


def register_file_upload_to_batch(responses, settings, *, succeeds: bool, batch_uid: str = BATCH_UID) -> None:
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
        url=f"https://api.smartling.com/job-batches-api/v2/projects/{quote(project_id)}/batches/{batch_uid}/file",
        body=json.dumps(body),
        status=status,
    )


def get_batch_uploads_from_responses(responses) -> list:
    """The per-file uploads, as opposed to the calls that create a batch or read its status."""
    return [call for call in responses.calls if call.request.url.endswith("/file")]


def request_body_of(responses, *, path_ends_with: str) -> dict:
    """The JSON body of the first request this run made to a path ending that way."""
    call = next(call for call in responses.calls if call.request.url.endswith(path_ends_with))
    return json.loads(call.request.body)


def two_file_job(root_page, **kwargs):
    """A job holding two pages, as an order of several pages produces."""
    return JobFactory(
        source_instance=root_page,
        files=[
            TranslationSourceFactory(source_instance=root_page),
            TranslationSourceFactory(source_instance=InfoPageFactory(parent=root_page, title="Second page")),
        ],
        **kwargs,
    )


def locales_and_files_requested(responses, *, path_ends_with: str) -> set:
    """The (locale, fileUri) pairs this run asked Smartling about at one endpoint."""
    pairs = set()
    for call in responses.calls:
        url = urlsplit(call.request.url)
        if not url.path.endswith(path_ends_with):
            continue
        locale_id = url.path.split("/locales/")[1].split("/")[0]
        pairs.add((locale_id, parse_qs(url.query).get("fileUri", [""])[0]))
    return pairs


def job_over_pages_with_content(root_page, titles, **kwargs):
    """
    A job holding a file per title, each with its page's real segments.

    TranslationSourceFactory stores no content, so a PO exported from one of
    its sources has nothing for an import to land on.
    """
    sources = [
        TranslationSource.get_or_create_from_instance(InfoPageFactory(parent=root_page, title=title))[0]
        for title in titles
    ]
    return JobFactory(source_instance=root_page, files=sources, **kwargs)


def po_with_same_answer_for_each_string(translation_source, msgstr: str) -> str:
    """The page's own PO, with the same answer against every one of its strings."""
    po_file = translation_source.export_po()
    for entry in po_file:
        entry.msgstr = msgstr
    return str(po_file)


def get_string_translations_for_page(translation_source, language_code: str) -> set:
    """What was imported for one page in one locale."""
    return set(
        StringTranslation.objects.filter(
            context__object_id=translation_source.object_id,
            locale__language_code=language_code,
        ).values_list("data", flat=True)
    )


def mark_files_uploaded(job) -> list:
    """Give each of a job's files the URI a finished upload would have left on it."""
    job_files = list(job.files.all())
    for job_file in job_files:
        job_file.file_uri = client.get_file_uri_for_job_file(job_file=job_file)
        job_file.save(update_fields=["file_uri"])
    return job_files


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
        _import_translation_for_locale(smartling_job, smartling_job.files.get(), translation, "fr")

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
        _import_translation_for_locale(smartling_job, smartling_job.files.get(), translation, "fr")


class TestPushIsIncomplete:
    def test_a_job_that_was_never_pushed_is_incomplete(self, root_page):
        assert JobFactory(source_instance=root_page, unsynced=True).push_is_incomplete

    def test_a_job_whose_upload_never_landed_is_incomplete(self, root_page):
        job = JobFactory(source_instance=root_page, synced=True)
        assert job.push_is_incomplete

    def test_a_job_with_only_some_of_its_files_uploaded_is_incomplete(self, root_page):
        """An order stops part-way through its pages, and the rest are still owed to Smartling."""
        job = two_file_job(root_page, synced=True)
        pushed_file, unsent_file = mark_files_uploaded(job)
        unsent_file.file_uri = ""
        unsent_file.save(update_fields=["file_uri"])

        assert job.push_is_incomplete

    def test_a_job_with_every_file_uploaded_is_complete(self, root_page):
        job = JobFactory(source_instance=root_page, synced=True)
        job.files.update(file_uri="job_1_ts_1.po")

        assert not job.push_is_incomplete


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


class TestInitialSyncIsResumable:
    """A push that stopped part-way is finished by the next sweep, not started over."""

    def test_every_file_of_the_job_is_uploaded(
        self, root_page, smartling_create_job, smartling_create_batch_for_job, responses, settings
    ):
        job = two_file_job(root_page, unsynced=True)
        register_file_upload_to_batch(responses, settings, succeeds=True)
        register_file_upload_to_batch(responses, settings, succeeds=True)

        _initial_sync(job)

        assert not job.files.filter(file_uri="").exists()
        assert len(get_batch_uploads_from_responses(responses)) == 2

    def test_the_job_is_created_for_the_union_of_its_files_locales(
        self, root_page, smartling_create_job, smartling_create_batch_for_job, responses, settings
    ):
        source_a = TranslationSourceFactory(source_instance=root_page)
        source_b = TranslationSourceFactory(source_instance=InfoPageFactory(parent=root_page, title="Second page"))
        job = JobFactory(source_instance=root_page, files=[source_a, source_b], translations=[], unsynced=True)
        job.translations.set(
            [
                Translation.objects.create(source=source_a, target_locale=Locale.objects.get(language_code="fr")),
                Translation.objects.create(source=source_b, target_locale=Locale.objects.get(language_code="de")),
            ]
        )
        register_file_upload_to_batch(responses, settings, succeeds=True)
        register_file_upload_to_batch(responses, settings, succeeds=True)

        _initial_sync(job)

        assert sorted(request_body_of(responses, path_ends_with="/jobs")["targetLocaleIds"]) == ["de", "fr"]

    def test_a_resumed_push_with_no_batch_of_its_own_opens_one_for_what_is_left(
        self, root_page, smartling_create_batch_for_job, responses, settings
    ):
        """No create-job response is registered, so a resume that asked for a second job would find none."""
        job = two_file_job(root_page, synced=True)
        landed, pending = job.files.all()
        landed.file_uri = f"job_{job.pk}_ts_{landed.translation_source_id}.po"
        landed.save(update_fields=["file_uri"])
        register_file_upload_to_batch(responses, settings, succeeds=True)

        _initial_sync(job)

        batch = request_body_of(responses, path_ends_with="/batches")
        assert batch["fileUris"] == [f"job_{job.pk}_ts_{pending.translation_source_id}.po"]
        assert len(get_batch_uploads_from_responses(responses)) == 1

    def test_the_job_records_a_file_uri_for_downloading(
        self, root_page, smartling_create_job, smartling_create_batch_for_job, responses, settings
    ):
        job = two_file_job(root_page, unsynced=True)
        register_file_upload_to_batch(responses, settings, succeeds=True)
        register_file_upload_to_batch(responses, settings, succeeds=True)

        _initial_sync(job)

        job.refresh_from_db()
        assert job.file_uri == job.files.first().file_uri

    def test_a_first_push_announces_the_job(
        self, root_page, smartling_create_job, smartling_create_batch_for_job, responses, settings
    ):
        job = JobFactory(source_instance=root_page, unsynced=True)
        register_file_upload_to_batch(responses, settings, succeeds=True)

        with patch("wagtail_localize_smartling.sync.job_synced_to_smartling.send") as announce:
            _initial_sync(job)

        announce.assert_called_once()

    def test_a_resumed_push_does_not_announce_the_job_again(
        self, root_page, smartling_create_batch_for_job, responses, settings
    ):
        job = two_file_job(root_page, synced=True)
        landed = job.files.first()
        landed.file_uri = f"job_{job.pk}_ts_{landed.translation_source_id}.po"
        landed.save(update_fields=["file_uri"])
        register_file_upload_to_batch(responses, settings, succeeds=True)

        with patch("wagtail_localize_smartling.sync.job_synced_to_smartling.send") as announce:
            _initial_sync(job)

        announce.assert_not_called()

    def test_a_file_with_no_locales_stops_the_push_before_anything_is_sent(self, root_page, responses):
        source_a = TranslationSourceFactory(source_instance=root_page)
        source_b = TranslationSourceFactory(source_instance=InfoPageFactory(parent=root_page, title="Second page"))
        job = JobFactory(source_instance=root_page, files=[source_a, source_b], translations=[], unsynced=True)
        job.translations.set(
            [Translation.objects.create(source=source_a, target_locale=Locale.objects.get(language_code="fr"))]
        )
        # What makes source_b's file unorderable is that this job asked for no
        # locale for it, not that nobody ever translated the page.
        Translation.objects.create(source=source_b, target_locale=Locale.objects.get(language_code="de"))

        with pytest.raises(ValueError):
            _initial_sync(job)

        job.refresh_from_db()
        assert [call.request.url for call in responses.calls] == []
        assert job.translation_job_uid == ""

    def test_a_job_with_no_files_is_never_created_in_smartling(self, root_page, responses):
        """An empty Smartling job cannot be authorized, and every later sweep would poll it for ever."""
        job = JobFactory(source_instance=root_page, files=[], unsynced=True)

        with pytest.raises(ValueError):
            _initial_sync(job)

        job.refresh_from_db()
        assert [call.request.url for call in responses.calls] == []
        assert job.translation_job_uid == ""

    def test_a_push_that_stops_part_way_keeps_the_files_that_landed(
        self, root_page, smartling_create_job, smartling_create_batch_for_job, responses, settings
    ):
        job = two_file_job(root_page, unsynced=True)
        register_file_upload_to_batch(responses, settings, succeeds=True)
        register_file_upload_to_batch(responses, settings, succeeds=False)

        with pytest.raises(SyncJobException):
            sync_job(job.pk)

        assert job.files.first().file_uri != ""
        assert job.files.last().file_uri == ""

    def test_the_job_is_announced_even_when_an_upload_then_fails(
        self, root_page, smartling_create_job, smartling_create_batch_for_job, responses, settings
    ):
        job = JobFactory(source_instance=root_page, unsynced=True)
        register_file_upload_to_batch(responses, settings, succeeds=False)

        with (
            patch("wagtail_localize_smartling.sync.job_synced_to_smartling.send") as announce,
            pytest.raises(SyncJobException),
        ):
            sync_job(job.pk)

        announce.assert_called_once()


class TestAPushResumesTheBatchItOpened:
    """
    A push that stopped part-way asks its open batch about each file, instead of
    opening a second batch.

    Smartling processes one batch per job at a time and refuses a file URI a
    batch already holds, so we determine what happened with the batch's files,
    and resume for any files that need to be resumed.
    """

    def test_the_batch_is_recorded_before_the_first_file_is_uploaded(
        self, root_page, smartling_create_job, smartling_create_batch_for_job, responses, settings
    ):
        job = JobFactory(source_instance=root_page, unsynced=True)
        batch_uid_while_uploading = None
        project_id = settings.WAGTAIL_LOCALIZE_SMARTLING["PROJECT_ID"]

        def accept_the_file(request):
            nonlocal batch_uid_while_uploading
            batch_uid_while_uploading = Job.objects.get(pk=job.pk).batch_uid
            return 202, {}, json.dumps({"response": {"code": "ACCEPTED", "data": {}}})

        responses.add_callback(
            method="POST",
            url=f"https://api.smartling.com/job-batches-api/v2/projects/{quote(project_id)}/batches/{BATCH_UID}/file",
            callback=accept_the_file,
        )

        _initial_sync(job)

        assert batch_uid_while_uploading == BATCH_UID

    def test_a_push_that_resumes_uploads_into_the_batch_it_already_opened(
        self, root_page, smartling_get_batch_status, responses, settings
    ):
        job = two_file_job(root_page, synced=True, batch_uid=EARLIER_BATCH_UID)
        landed, pending = job.files.all()
        landed.file_uri = client.get_file_uri_for_job_file(job_file=landed)
        landed.save(update_fields=["file_uri"])
        smartling_get_batch_status(
            batch_uid=EARLIER_BATCH_UID,
            status="ADDING_FILES",
            files=[{"fileUri": landed.file_uri, "status": "COMPLETED", "errors": ""}],
        )
        register_file_upload_to_batch(responses, settings, succeeds=True, batch_uid=EARLIER_BATCH_UID)

        _initial_sync(job)

        assert len(get_batch_uploads_from_responses(responses)) == 1
        pending.refresh_from_db()
        assert pending.file_uri == client.get_file_uri_for_job_file(job_file=pending)

    def test_a_file_smartling_already_has_is_recorded_rather_than_uploaded_again(
        self, root_page, smartling_get_batch_status, responses
    ):
        job = JobFactory(source_instance=root_page, synced=True, batch_uid=EARLIER_BATCH_UID)
        pending = job.files.get()
        file_uri = client.get_file_uri_for_job_file(job_file=pending)
        smartling_get_batch_status(
            batch_uid=EARLIER_BATCH_UID,
            status="ADDING_FILES",
            files=[{"fileUri": file_uri, "status": "COMPLETED", "errors": ""}],
        )

        _initial_sync(job)

        pending.refresh_from_db()
        job.refresh_from_db()
        assert pending.file_uri == file_uri
        assert job.file_uri == file_uri
        assert get_batch_uploads_from_responses(responses) == []

    def test_a_file_the_batch_is_still_reading_is_left_for_the_next_sweep(
        self, root_page, smartling_get_batch_status, responses
    ):
        job = JobFactory(source_instance=root_page, synced=True, batch_uid=EARLIER_BATCH_UID)
        pending = job.files.get()
        smartling_get_batch_status(
            batch_uid=EARLIER_BATCH_UID,
            status="ADDING_FILES",
            files=[
                {
                    "fileUri": client.get_file_uri_for_job_file(job_file=pending),
                    "status": "UPLOADING",
                    "errors": "",
                }
            ],
        )

        _initial_sync(job)

        pending.refresh_from_db()
        assert pending.file_uri == ""
        assert get_batch_uploads_from_responses(responses) == []

    def test_a_file_in_a_state_we_do_not_recognise_is_left_for_the_next_sweep(
        self, root_page, smartling_get_batch_status, responses
    ):
        job = JobFactory(source_instance=root_page, synced=True, batch_uid=EARLIER_BATCH_UID)
        pending = job.files.get()
        smartling_get_batch_status(
            batch_uid=EARLIER_BATCH_UID,
            status="ADDING_FILES",
            files=[
                {
                    "fileUri": client.get_file_uri_for_job_file(job_file=pending),
                    "status": "ATTACHING",
                    "errors": "",
                }
            ],
        )

        _initial_sync(job)

        pending.refresh_from_db()
        assert pending.file_uri == ""
        assert get_batch_uploads_from_responses(responses) == []

    def test_a_file_a_completed_batch_never_received_goes_into_a_new_batch(
        self, root_page, smartling_get_batch_status, smartling_create_batch_for_job, responses, settings
    ):
        job = JobFactory(source_instance=root_page, synced=True, batch_uid=EARLIER_BATCH_UID)
        pending = job.files.get()
        smartling_get_batch_status(batch_uid=EARLIER_BATCH_UID, status="COMPLETED", files=[])
        register_file_upload_to_batch(responses, settings, succeeds=True)
        assert job.batch_uid != BATCH_UID

        _initial_sync(job)

        job.refresh_from_db()
        pending.refresh_from_db()
        assert job.batch_uid == BATCH_UID
        assert pending.file_uri == client.get_file_uri_for_job_file(job_file=pending)
        assert request_body_of(responses, path_ends_with="/batches")["fileUris"] == [pending.file_uri]

    def test_a_file_a_completed_batch_did_receive_is_not_sent_a_second_time(
        self, root_page, smartling_get_batch_status, responses
    ):
        job = JobFactory(source_instance=root_page, synced=True, batch_uid=EARLIER_BATCH_UID)
        pending = job.files.get()
        file_uri = client.get_file_uri_for_job_file(job_file=pending)
        smartling_get_batch_status(
            batch_uid=EARLIER_BATCH_UID,
            status="COMPLETED",
            files=[{"fileUri": file_uri, "status": "COMPLETED", "errors": ""}],
        )

        _initial_sync(job)

        pending.refresh_from_db()
        assert pending.file_uri == file_uri
        assert get_batch_uploads_from_responses(responses) == []

    def test_a_batch_smartling_no_longer_has_is_replaced(
        self,
        root_page,
        smartling_get_batch_status__not_found,
        smartling_create_batch_for_job,
        responses,
        settings,
    ):
        job = JobFactory(source_instance=root_page, synced=True, batch_uid=EARLIER_BATCH_UID)
        pending = job.files.get()
        smartling_get_batch_status__not_found(batch_uid=EARLIER_BATCH_UID)
        register_file_upload_to_batch(responses, settings, succeeds=True)

        _initial_sync(job)

        job.refresh_from_db()
        pending.refresh_from_db()
        assert job.batch_uid == BATCH_UID
        assert pending.file_uri == client.get_file_uri_for_job_file(job_file=pending)

    def test_an_executing_batch_is_left_alone_until_it_finishes(self, root_page, smartling_get_batch_status, responses):
        job = JobFactory(source_instance=root_page, synced=True, batch_uid=EARLIER_BATCH_UID)
        pending = job.files.get()
        smartling_get_batch_status(batch_uid=EARLIER_BATCH_UID, status="EXECUTING", files=[])

        sync_job(job.pk)

        job.refresh_from_db()
        pending.refresh_from_db()
        assert pending.file_uri == ""
        assert get_batch_uploads_from_responses(responses) == []
        assert job.consecutive_sync_failures == 0
        assert not job.sync_blocked

    def test_a_file_cancelled_inside_a_live_batch_in_smarling_waits_for_the_batch_to_finish(
        self, root_page, smartling_get_batch_status, responses
    ):
        """
        Cancelling a file (in Smartling) is what lets a batch run without it, so
        the batch will finish and it can be re-sent.
        """
        job = JobFactory(source_instance=root_page, synced=True, batch_uid=EARLIER_BATCH_UID)
        pending = job.files.get()
        smartling_get_batch_status(
            batch_uid=EARLIER_BATCH_UID,
            status="ADDING_FILES",
            files=[
                {
                    "fileUri": client.get_file_uri_for_job_file(job_file=pending),
                    "status": "CANCELED",
                    "errors": "",
                }
            ],
        )

        sync_job(job.pk)

        job.refresh_from_db()
        pending.refresh_from_db()
        assert not job.sync_blocked
        assert job.consecutive_sync_failures == 0
        assert pending.file_uri == ""
        assert get_batch_uploads_from_responses(responses) == []

    def test_visual_context_waits_until_every_file_has_been_sent(
        self, root_page, smartling_get_batch_status, responses
    ):
        job = two_file_job(root_page, synced=True, batch_uid=EARLIER_BATCH_UID)
        landed, pending = job.files.all()
        landed.file_uri = client.get_file_uri_for_job_file(job_file=landed)
        landed.save(update_fields=["file_uri"])
        smartling_get_batch_status(
            batch_uid=EARLIER_BATCH_UID,
            status="EXECUTING",
            files=[{"fileUri": landed.file_uri, "status": "COMPLETED", "errors": ""}],
        )

        with patch("wagtail_localize_smartling.sync.client.add_html_context_to_job") as add_context:
            _initial_sync(job)

        add_context.assert_not_called()

    def test_a_file_that_failed_inside_a_live_batch_blocks_the_job(
        self, root_page, smartling_get_batch_status, responses
    ):
        job = JobFactory(source_instance=root_page, synced=True, batch_uid=EARLIER_BATCH_UID)
        pending = job.files.get()
        smartling_get_batch_status(
            batch_uid=EARLIER_BATCH_UID,
            status="ADDING_FILES",
            files=[
                {
                    "fileUri": client.get_file_uri_for_job_file(job_file=pending),
                    "status": "FAILED",
                    "errors": "could not parse",
                }
            ],
        )

        with pytest.raises(SyncJobException):
            sync_job(job.pk)

        job.refresh_from_db()
        assert job.sync_blocked
        assert job.consecutive_sync_failures == 1
        assert get_batch_uploads_from_responses(responses) == []


class TestPollingIsPerFile:
    @pytest.mark.parametrize(
        "finished_position", [0, 1], ids=["the job's first file finishes", "the job's second file finishes"]
    )
    def test_a_locale_lands_for_whichever_file_finished(
        self,
        finished_position,
        root_page,
        smartling_get_file_status,
        smartling_download_translation_for_locale,
        responses,
        disable_signals,
    ):
        """
        One page of an order can be finished while another is still being
        worked on, so a locale is complete for a file, not for the job.

        Each of the job's two files takes a turn at being the finished one,
        because code that reached for a file by position rather than by page
        would serve whichever of the two happened to sit there.
        """
        job = two_file_job(root_page, synced=True, status=JobStatus.IN_PROGRESS)
        mark_files_uploaded(job)
        job_files = list(job.files.order_by("pk"))
        finished_file = job_files.pop(finished_position)
        unfinished_file = job_files[0]
        # Nor is there a job-level URI to reach either file by.
        assert job.file_uri == ""

        smartling_get_file_status("fr", total_strings=10, completed_strings=10, file_uri=finished_file.file_uri)
        smartling_get_file_status("de", total_strings=10, completed_strings=4, file_uri=finished_file.file_uri)
        smartling_get_file_status("fr", total_strings=10, completed_strings=4, file_uri=unfinished_file.file_uri)
        smartling_get_file_status("de", total_strings=10, completed_strings=4, file_uri=unfinished_file.file_uri)
        smartling_download_translation_for_locale("fr", file_uri=finished_file.file_uri)

        imported = _check_and_import_completed_locales(job)

        assert [(translation.source.pk, translation.target_locale.language_code) for translation in imported] == [
            (finished_file.translation_source_id, "fr")
        ]
        # Both files were asked about, each under its own URI, in both of the
        # locales it was ordered in.
        assert locales_and_files_requested(responses, path_ends_with="/file/status") == {
            ("fr", unfinished_file.file_uri),
            ("de", unfinished_file.file_uri),
            ("fr", finished_file.file_uri),
            ("de", finished_file.file_uri),
        }
        # Only the file Smartling called finished was downloaded, and the PO
        # fetched was that file's, not the job's first file's.
        assert locales_and_files_requested(responses, path_ends_with="/file") == {("fr", finished_file.file_uri)}

    def test_a_file_that_never_reached_smartling_is_not_checked(
        self,
        root_page,
        smartling_get_file_status,
        smartling_download_translation_for_locale,
        responses,
        disable_signals,
    ):
        """Its upload is what failed, so Smartling knows nothing about it and the rest of the job still lands."""
        job = two_file_job(root_page, synced=True, status=JobStatus.IN_PROGRESS, translations=[])
        pushed_file, unsent_file = mark_files_uploaded(job)
        unsent_file.file_uri = ""
        unsent_file.save(update_fields=["file_uri"])
        french = Locale.objects.get(language_code="fr")
        job.translations.add(
            Translation.objects.create(source=pushed_file.translation_source, target_locale=french),
            Translation.objects.create(source=unsent_file.translation_source, target_locale=french),
        )
        smartling_get_file_status("fr", total_strings=10, completed_strings=10, file_uri=pushed_file.file_uri)
        smartling_download_translation_for_locale("fr", file_uri=pushed_file.file_uri)

        imported = _check_and_import_completed_locales(job)

        assert [translation.source.pk for translation in imported] == [pushed_file.translation_source_id]
        assert locales_and_files_requested(responses, path_ends_with="/file/status") == {("fr", pushed_file.file_uri)}

    def test_a_translation_belonging_to_no_file_is_skipped(
        self,
        root_page,
        smartling_get_file_status,
        smartling_download_translation_for_locale,
        responses,
        disable_signals,
    ):
        """
        There is no file to ask Smartling about, so the rest of the job is
        imported and the stray translation is left pending.
        """
        job = JobFactory(source_instance=root_page, synced=True, status=JobStatus.IN_PROGRESS, translations=[])
        job_file = mark_files_uploaded(job)[0]
        job.translations.add(
            Translation.objects.create(
                source=job_file.translation_source,
                target_locale=Locale.objects.get(language_code="fr"),
            ),
            # This translation does not belong to any JobFile, so it is skipped.
            Translation.objects.create(
                source=TranslationSourceFactory(
                    source_instance=InfoPageFactory(parent=root_page, title="Never ordered")
                ),
                target_locale=Locale.objects.get(language_code="fr"),
            ),
        )
        smartling_get_file_status("fr", total_strings=10, completed_strings=10, file_uri=job_file.file_uri)
        smartling_download_translation_for_locale("fr", file_uri=job_file.file_uri)

        imported = _check_and_import_completed_locales(job)

        assert [translation.source.pk for translation in imported] == [job_file.translation_source_id]
        assert JobTranslation.objects.filter(job=job, imported_at__isnull=True).count() == 1
        # Nothing was asked about on the stray translation's behalf.
        assert locales_and_files_requested(responses, path_ends_with="/file/status") == {("fr", job_file.file_uri)}


class TestDownloadIsPerFile:
    def test_each_zip_entry_is_imported_into_its_own_page(self, root_page, smartling_translations_zip, disable_signals):
        job = job_over_pages_with_content(root_page, ["First ordered page", "Second ordered page"], synced=True)
        first_file, second_file = mark_files_uploaded(job)
        first_po = po_with_same_answer_for_each_string(first_file.translation_source, "Première page")
        second_po = po_with_same_answer_for_each_string(second_file.translation_source, "Deuxième page")
        smartling_translations_zip(file_uri=first_file.file_uri, entries={f"fr/{first_file.file_uri}": first_po})
        smartling_translations_zip(file_uri=second_file.file_uri, entries={f"fr/{second_file.file_uri}": second_po})

        _download_and_apply_translations(job)

        assert get_string_translations_for_page(first_file.translation_source, "fr") == {"Première page"}
        assert get_string_translations_for_page(second_file.translation_source, "fr") == {"Deuxième page"}

    def test_each_file_is_downloaded_under_its_own_uri(
        self, root_page, smartling_translations_zip, responses, disable_signals
    ):
        job = two_file_job(root_page, synced=True)
        first_file, second_file = mark_files_uploaded(job)
        for job_file in (first_file, second_file):
            smartling_translations_zip(file_uri=job_file.file_uri, entries={})

        with patch("wagtail_localize_smartling.sync.translation_import_successful") as announce:
            _download_and_apply_translations(job)

        assert locales_and_files_requested(responses, path_ends_with="/file/zip") == {
            ("all", first_file.file_uri),
            ("all", second_file.file_uri),
        }
        # Smartling had nothing for either file, so nobody is told a
        # translation landed.
        announce.send.assert_not_called()

    def test_a_file_that_never_reached_smartling_is_not_downloaded(
        self, root_page, smartling_translations_zip, responses, disable_signals
    ):
        job = two_file_job(root_page, synced=True)
        pushed_file, unsent_file = mark_files_uploaded(job)
        unsent_file.file_uri = ""
        unsent_file.save(update_fields=["file_uri"])
        smartling_translations_zip(file_uri=pushed_file.file_uri, entries={})

        _download_and_apply_translations(job)

        assert locales_and_files_requested(responses, path_ends_with="/file/zip") == {("all", pushed_file.file_uri)}

    def test_an_entry_for_a_file_outside_the_job_is_loud(self, root_page, smartling_translations_zip, disable_signals):
        """
        Smartling answered with a file this job does not hold, so which page it
        belongs to is unknown and importing it anywhere would be a guess.
        """
        job = JobFactory(source_instance=root_page, synced=True)
        job_file = mark_files_uploaded(job)[0]
        smartling_translations_zip(
            file_uri=job_file.file_uri,
            entries={"fr/job_999_ts_999.po": po_with_same_answer_for_each_string(job_file.translation_source, "Nope")},
        )

        with pytest.raises(FileURIMismatch):
            _download_and_apply_translations(job)

    def test_a_stray_entry_does_not_abandon_the_rest_of_the_order(
        self, root_page, smartling_translations_zip, disable_signals
    ):
        """
        The stray entry is deterministic, so a run that stopped at it would
        leave every page after it unimported on this run and on every later
        one, and would never announce the pages that did come back.
        """
        job = job_over_pages_with_content(root_page, ["Page whose zip goes wrong", "Page behind it"], synced=True)
        # Files are walked in pk order, so the stray entry arrives before the
        # page that has to survive it.
        first_file, later_file = mark_files_uploaded(job)
        later_po = po_with_same_answer_for_each_string(later_file.translation_source, "Page intacte")
        # Nothing about the file itself is stray, but its zip carries an entry
        # named for a file URI that no file of this job has.
        assert not job.files.filter(file_uri="job_999_ts_999.po").exists()
        smartling_translations_zip(file_uri=first_file.file_uri, entries={"fr/job_999_ts_999.po": 'msgid ""\n'})
        smartling_translations_zip(file_uri=later_file.file_uri, entries={f"fr/{later_file.file_uri}": later_po})

        with (
            patch("wagtail_localize_smartling.sync.translation_import_successful") as announce,
            pytest.raises(FileURIMismatch),
        ):
            _download_and_apply_translations(job)

        assert get_string_translations_for_page(later_file.translation_source, "fr") == {"Page intacte"}
        announce.send.assert_called_once()
        assert [translation.source.pk for translation in announce.send.call_args.kwargs["translations_imported"]] == [
            later_file.translation_source_id
        ]

    def test_an_entry_is_not_imported_into_another_page_that_shares_its_locale(
        self, root_page, smartling_translations_zip, disable_signals
    ):
        """
        Pages of one order can be ordered in different locales. This file was
        not ordered in French and another page of the job was, so a lookup by
        locale alone would mark that page's translation imported from this
        file's content, with nothing to show it had happened.
        """
        job = job_over_pages_with_content(
            root_page, ["Page ordered in French", "Page ordered in German"], synced=True, translations=[]
        )
        french_file, german_file = mark_files_uploaded(job)
        french_translation = Translation.objects.create(
            source=french_file.translation_source, target_locale=Locale.objects.get(language_code="fr")
        )
        job.translations.set(
            [
                french_translation,
                Translation.objects.create(
                    source=german_file.translation_source, target_locale=Locale.objects.get(language_code="de")
                ),
            ]
        )
        # Smartling answers for the German page in a locale only the French
        # page was ordered in.
        smartling_translations_zip(file_uri=french_file.file_uri, entries={})
        smartling_translations_zip(
            file_uri=german_file.file_uri,
            entries={
                # The German file's response in the French locale. This is a
                # mismatch, and we don't import the data into another page.
                f"fr/{german_file.file_uri}": po_with_same_answer_for_each_string(
                    german_file.translation_source, "Nope"
                )
            },
        )

        _download_and_apply_translations(job)

        assert JobTranslation.objects.get(job=job, translation=french_translation).imported_at is None
        assert get_string_translations_for_page(french_file.translation_source, "fr") == set()


def smartling_error_body(key: str, message: str) -> str:
    return json.dumps({"response": {"code": "VALIDATION_ERROR", "errors": [{"key": key, "message": message}]}})


def register_create_job_rejection(responses, settings, *, message: str) -> None:
    """Have Smartling reject the job, the way a duplicate name is rejected."""
    project_id = settings.WAGTAIL_LOCALIZE_SMARTLING["PROJECT_ID"]
    responses.add(
        method="POST",
        url=f"https://api.smartling.com/jobs-api/v3/projects/{quote(project_id)}/jobs",
        body=smartling_error_body("name", message),
        status=400,
    )


@pytest.mark.parametrize(
    ("exc", "expected_failure_permanent"),
    [
        (FailedResponse(code="VALIDATION_ERROR", errors=[], status_code=400), True),
        (FailedResponse(code="NOT_FOUND", errors=[], status_code=404), True),
        (FailedResponse(code="AUTH", errors=[], status_code=401), False),
        (FailedResponse(code="FORBIDDEN", errors=[], status_code=403), False),
        (FailedResponse(code="TIMEOUT", errors=[], status_code=408), False),
        (FailedResponse(code="CONFLICT", errors=[], status_code=409), False),
        (FailedResponse(code="RATE", errors=[], status_code=429), False),
        (FailedResponse(code="SERVER", errors=[], status_code=503), False),
        (FailedResponse(code="UNKNOWN", errors=[], status_code=None), False),
        (TimeoutError("timed out"), False),
    ],
)
def test_is_failure_permanent(exc, expected_failure_permanent):
    assert is_failure_permanent(exc) is expected_failure_permanent


class TestSyncFailureRecording:
    def test_a_rejected_submission_is_recorded_and_blocked(self, root_page, smartling_project, responses, settings):
        register_create_job_rejection(responses, settings, message="duplicate")
        job = JobFactory(source_instance=root_page, unsynced=True)

        with pytest.raises(SyncJobException):
            sync_job(job.pk)

        job.refresh_from_db()
        assert job.sync_blocked is True
        assert "duplicate" in job.last_sync_error

    def test_a_timeout_is_recorded_without_blocking(self, root_page, smartling_project):
        job = JobFactory(source_instance=root_page, unsynced=True)

        with (
            patch("wagtail_localize_smartling.sync.client.create_job", side_effect=TimeoutError("timed out")),
            pytest.raises(SyncJobException),
        ):
            sync_job(job.pk)

        job.refresh_from_db()
        assert job.sync_blocked is False
        assert job.consecutive_sync_failures == 1

    def test_a_successful_sync_forgets_an_earlier_failure(
        self,
        root_page,
        smartling_project,
        smartling_create_job,
        smartling_create_batch_for_job,
        responses,
        settings,
    ):
        register_file_upload_to_batch(responses, settings, succeeds=True)
        job = JobFactory(source_instance=root_page, unsynced=True)
        job.record_sync_failure(TimeoutError("timed out"), permanent=False, pushing=True)

        sync_job(job.pk)

        job.refresh_from_db()
        assert job.consecutive_sync_failures == 0
        assert job.last_sync_error == ""
        assert job.last_sync_error_at is None

    def test_a_visual_context_failure_does_not_block_a_submitted_job(
        self,
        root_page,
        smartling_project,
        smartling_create_job,
        smartling_create_batch_for_job,
        smartling_settings,
        responses,
        settings,
    ):
        """Context is best-effort; the files are already at Smartling by the time it is sent."""
        register_file_upload_to_batch(responses, settings, succeeds=True)
        smartling_settings.VISUAL_CONTEXT_CALLBACK = lambda job, translation_source: (
            "https://example.com/page/",
            "<html></html>",
        )
        project_id = settings.WAGTAIL_LOCALIZE_SMARTLING["PROJECT_ID"]
        responses.add(
            method="POST",
            url=f"https://api.smartling.com/context-api/v2/projects/{quote(project_id)}/contexts/upload-and-match-async",
            body=smartling_error_body("context", "no context for you"),
            status=400,
        )
        job = JobFactory(source_instance=root_page, unsynced=True)

        sync_job(job.pk)

        job.refresh_from_db()
        assert job.sync_blocked is False
        assert job.files.filter(file_uri="").count() == 0

    def test_a_failed_poll_does_not_block_the_job(self, root_page, smartling_project):
        """A job whose files are at Smartling is polled again regardless, so blocking it stops nothing."""
        job = JobFactory(source_instance=root_page, synced=True)
        job.files.update(file_uri="job.po")

        with (
            patch(
                "wagtail_localize_smartling.sync.client.get_job_details",
                side_effect=FailedResponse(code="NOT_FOUND", errors=[], status_code=404),
            ),
            pytest.raises(SyncJobException),
        ):
            sync_job(job.pk)

        job.refresh_from_db()
        assert job.sync_blocked is False
        assert job.consecutive_sync_failures == 1
        assert "NOT_FOUND" in job.last_sync_error

    def test_repeated_failed_polls_do_not_block_the_job_at_the_limit(
        self, root_page, smartling_project, smartling_settings
    ):
        """An outage lasting longer than the limit must not strand a job that is still translating."""
        smartling_settings.SYNC_FAILURE_LIMIT = 2
        job = JobFactory(source_instance=root_page, synced=True)
        job.files.update(file_uri="job.po")

        for _ in range(2):
            with (
                patch(
                    "wagtail_localize_smartling.sync.client.get_job_details",
                    side_effect=TimeoutError("smartling unreachable"),
                ),
                pytest.raises(SyncJobException),
            ):
                sync_job(job.pk)

        job.refresh_from_db()
        assert job.consecutive_sync_failures == 2
        assert job.sync_blocked is False

    def test_a_clean_poll_does_not_unblock_a_job_whose_push_never_finished(self, root_page, smartling_project):
        job = JobFactory(source_instance=root_page, synced=True)
        job.record_sync_failure(
            FailedResponse(code="VALIDATION_ERROR", errors=[], status_code=400), permanent=True, pushing=True
        )
        assert job.files.filter(file_uri="").exists(), "the upload that failed is what blocked the job"
        job_details = {
            "jobStatus": JobStatus.AWAITING_AUTHORIZATION,
            "description": "",
            "referenceNumber": "",
            "dueDate": None,
        }

        # Nothing stubs the push, so retrying it would fail the call outright.
        with patch("wagtail_localize_smartling.sync.client.get_job_details", return_value=job_details):
            sync_job(job.pk)

        job.refresh_from_db()
        assert job.sync_blocked is True
        assert job.consecutive_sync_failures == 1

    def test_a_clean_poll_clears_a_failure_once_the_push_has_finished(self, root_page, smartling_project):
        """Nothing is left to retry, so the job must stop reading as a failing one."""
        job = JobFactory(source_instance=root_page, synced=True)
        job.files.update(file_uri="job.po")
        job.record_sync_failure(
            FailedResponse(code="VALIDATION_ERROR", errors=[], status_code=400), permanent=True, pushing=True
        )
        job_details = {
            "jobStatus": JobStatus.AWAITING_AUTHORIZATION,
            "description": "",
            "referenceNumber": "",
            "dueDate": None,
        }

        with patch("wagtail_localize_smartling.sync.client.get_job_details", return_value=job_details):
            sync_job(job.pk)

        job.refresh_from_db()
        assert job.sync_blocked is False
        assert job.consecutive_sync_failures == 0
        assert job.last_sync_error == ""
        assert job.last_sync_error_at is None


class TestAJobDeletedInSmartling:
    """Smartling answering "no such job" is an answer about the job, not a sync failure."""

    def deleted_in_smartling(self):
        return patch(
            "wagtail_localize_smartling.sync.client.get_job_details",
            side_effect=SmartlingJobNotFound("Job smartling-job-1 not found"),
        )

    def test_the_job_is_marked_deleted(self, root_page, smartling_project):
        job = JobFactory(source_instance=root_page, synced=True)
        job.files.update(file_uri="job.po")

        with self.deleted_in_smartling():
            sync_job(job.pk)

        job.refresh_from_db()
        assert job.status == JobStatus.DELETED

    def test_no_sync_failure_is_recorded(self, root_page, smartling_project):
        job = JobFactory(source_instance=root_page, synced=True)
        job.files.update(file_uri="job.po")

        with self.deleted_in_smartling():
            sync_job(job.pk)

        job.refresh_from_db()
        assert job.consecutive_sync_failures == 0
        assert job.last_sync_error == ""
        assert job.sync_blocked is False

    def test_the_job_needs_a_new_one(self, root_page, smartling_project):
        job = JobFactory(source_instance=root_page, synced=True)
        job.files.update(file_uri="job.po")

        with self.deleted_in_smartling():
            sync_job(job.pk)

        job.refresh_from_db()
        assert job.needs_a_new_job is True
