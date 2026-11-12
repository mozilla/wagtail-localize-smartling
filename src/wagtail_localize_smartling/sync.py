import hashlib
import logging

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import polib

from django.db import transaction
from django.utils import timezone
from wagtail_localize.models import Translation

from . import utils
from .api.client import BatchNotFound as SmartlingBatchNotFound
from .api.client import JobNotFound as SmartlingJobNotFound
from .api.client import client
from .api.types import GetBatchStatusResponseData, JobStatus
from .constants import PENDING_STATUSES, TRANSLATED_STATUSES, UNTRANSLATED_STATUSES
from .settings import settings as smartling_settings
from .signals import (
    individual_translation_imported,
    job_sync_failed,
    job_synced_to_smartling,
    translation_import_successful,
)


def _sanitize_po_content(content: str) -> str:
    """
    Remove Unicode line/paragraph separators (U+2028, U+2029) from PO content.

    Python's str.splitlines() treats these as line boundaries, but polib uses
    splitlines() internally to tokenize string input. If these characters appear
    inside a quoted msgstr value, polib's parser sees a broken line and raises
    a syntax error. Replacing them with a regular space preserves word boundaries
    without affecting the PO file structure.
    """
    return content.replace("\u2028", " ").replace("\u2029", " ")


def _compute_translation_hash(content: str) -> str:
    """
    Compute a hash of translated PO file content.

    Unlike compute_content_hash in utils.py (which only hashes source msgid/msgctxt),
    this includes msgstr to detect changes in translations. It ignores header metadata
    (like timestamps) to ensure the hash is stable across downloads.
    """
    po = polib.pofile(_sanitize_po_content(content))
    strings = []
    for entry in po:
        strings.append(f"{entry.msgctxt}:{entry.msgid}:{entry.msgstr}")
    return hashlib.sha256("".join(strings).encode()).hexdigest()


if TYPE_CHECKING:
    from .models import Job, JobFile, JobTranslation


logger = logging.getLogger(__name__)


class SyncJobException(Exception):
    pass


class LocalJobNotFound(SyncJobException):
    """The Job row is gone from the database; Smartling is not involved."""


class FileURIMismatch(SyncJobException):
    """Smartling answered with a file URI that belongs to no file of the job."""


class BatchFileUnusable(SyncJobException):
    """The batch a job has opened can no longer carry a file the job still owes Smartling."""


# How long a claim on a Job is honoured before another worker may take it over.
# Long enough for a large order's uploads, short enough that a worker killed
# mid-push does not strand the job until someone notices.
SYNC_LEASE_SECONDS = 60 * 30


# Statuses where a 4xx says something about the caller, the moment, or the
# network, rather than about the job, so retrying is the right move.
RETRYABLE_CLIENT_ERROR_STATUSES = frozenset({401, 403, 408, 409, 429})


# Batch states, from the Job Batches V2 API.
# https://api-reference.smartling.com/#tag/Job-Batches-V2/operation/getJobBatchStatusV2
# DRAFT and ADDING_FILES both accept files, so neither needs a branch of its own.
BATCH_EXECUTING = "EXECUTING"
BATCH_COMPLETED = "COMPLETED"

# Per-file states within a batch. A file in one of these reached Smartling,
# whether or not our record of its upload survived.
BATCH_ITEM_STATUSES_THAT_LANDED = frozenset({"UPLOADED", "PROCESSING", "COMPLETED"})
BATCH_ITEM_FAILED = "FAILED"
# Cancelling all non-completed files allows a batch to complete, so we have
# logic for the batch item cancelled state.
BATCH_ITEM_CANCELED = "CANCELED"


def is_failure_permanent(exc: Exception) -> bool:
    """
    Return True if this failure will recur on every retry.

    A 4xx means Smartling rejected the request itself (for example, for a
    duplicate job name, a job with no strings, an unknown locale), and will
    reject it again.
    """
    # Smartling could not read the file, and will not read it any better next
    # time.
    if isinstance(exc, BatchFileUnusable):
        return True

    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        return False
    if status_code in RETRYABLE_CLIENT_ERROR_STATUSES:
        return False
    return 400 <= status_code < 500


def sync_job(job_id: int) -> None:
    """
    Sync the state of a Job instance with the corresponding job in Smartling.

    One worker syncs a job at a time; a call made while another is part-way
    through returns having done nothing.

    NB - this takes an ID, rather than a job instance, so that it's always
    operating on current data rather than pickled state. That means it's safe
    to be called after arbitrary time from the background task queue, if one
    is in use.
    """
    # We claim a job for the sync, so that we properly handle any uploads that
    # fail.
    job = _claim_job_for_sync(job_id)
    if job is None:
        logger.info("Job %s is already being synced, skipping", job_id)
        return

    claimed_at = job.sync_started_at
    was_blocked = job.sync_blocked
    # A blocked job is never pushed again: its push is what failed, and nothing
    # here can fix it.
    pushing = not was_blocked and job.push_is_incomplete
    try:
        if pushing:
            _initial_sync(job)
        elif job.translation_job_uid:
            _sync(job)
        # A blocked job with no uid never reached Smartling, so there is
        # nothing to poll and nothing worth pushing until the block is cleared.
    except Exception as e:
        permanent = is_failure_permanent(e)
        job.record_sync_failure(e, permanent=permanent, pushing=pushing)

        # Past the limit the job keeps being swept but stops being announced:
        # a job that fails every sweep would otherwise alert every sweep.
        if job.consecutive_sync_failures <= smartling_settings.SYNC_FAILURE_LIMIT:
            # Sent a job_sync_failed signal robustly, so that this exception
            # reaches the caller.
            for receiver, response in job_sync_failed.send_robust(
                sender=job.__class__,
                instance=job,
                exception=e,
                permanent=permanent,
            ):
                if isinstance(response, Exception):
                    logger.error(
                        "%s could not handle the failed sync of job %s",
                        receiver,
                        job,
                        exc_info=response,
                    )
        raise SyncJobException(f"Exception syncing job {job}") from e
    else:
        # A job blocked on its push stays blocked until that push finishes: a
        # clean poll says nothing about the upload that failed.
        if not was_blocked or not job.push_is_incomplete:
            job.clear_sync_failure()
    finally:
        try:
            _release_claim(job, claimed_at=claimed_at)
        except Exception:
            # The lease expires on its own, so failing to clear it must neither
            # mask the outcome of the sync nor stop the caller's next job.
            logger.exception("Could not release the sync claim on job %s", job)


@transaction.atomic
def _claim_job_for_sync(job_id: int) -> "Job | None":
    """Take the sync lease on a job, or return None if another worker holds it."""
    # Imported here because models imports this module for sync_job.
    from .models import Job

    try:
        job = Job.objects.select_for_update().get(pk=job_id)
    except Job.DoesNotExist as e:
        raise LocalJobNotFound(f"Job with ID {job_id} not found") from e

    # A Job is already being synced (by another worker), so return None, and let
    # that worker finish.
    beginning_of_sync_lease_window = timezone.now() - timedelta(seconds=SYNC_LEASE_SECONDS)
    if job.sync_started_at is not None and job.sync_started_at > beginning_of_sync_lease_window:
        return None

    job.sync_started_at = timezone.now()
    job.save(update_fields=["sync_started_at"])
    return job


def _release_claim(job: "Job", *, claimed_at: datetime) -> None:
    """
    Give up the sync lease, unless someone else has already taken it over.

    A push that outruns SYNC_LEASE_SECONDS is claimed by the next worker while
    the first is still going; the first must not then clear the second's claim.
    Everything that writes to the job while it pushes therefore has to leave
    sync_started_at alone, or it would put its own claim back on the row.
    """
    from .models import Job

    Job.objects.filter(pk=job.pk, sync_started_at=claimed_at).update(sync_started_at=None)


def _check_job_can_be_authorized(job: "Job") -> None:
    """
    Raise unless the job has files and every one of them is ordered in at least
    one locale.
    """
    if not job.files.exists():
        raise ValueError(f"No files to translate in {job}")

    sources_with_locales = set(job.translations.values_list("source_id", flat=True))
    files_without_locales = [
        job_file for job_file in job.files.all() if job_file.translation_source_id not in sources_with_locales
    ]
    if files_without_locales:
        # Named by file URI, which is how the upload itself refuses them.
        file_uris = ", ".join(client.get_file_uri_for_job_file(job_file=job_file) for job_file in files_without_locales)
        raise ValueError(f"No locales to authorize for {file_uris}")


def _initial_sync(job: "Job") -> None:
    """
    Create the job in Smartling, if it is not there yet, and upload a PO file
    for each of the job's files that has not been uploaded already.

    Safe to call again on a job whose push stopped part-way through: it sends
    what is still missing rather than ordering the whole job a second time.

    Also add Visual Context for Smartling CAT, if a callback to get that is configured
    """
    logger.info("Performing initial sync for job %s", job)

    _check_job_can_be_authorized(job)

    if not job.translation_job_uid:
        # Every locale any of the job's files was ordered in. Each file is
        # then authorized for only its own subset when it is uploaded.
        target_locale_ids = [
            utils.format_smartling_locale_id(lc)
            for lc in job.translations.values_list("target_locale__language_code", flat=True)
        ]

        job_data = client.create_job(
            job_name=job.name,
            target_locale_ids=target_locale_ids,
            description=job.description,
            reference_number=job.reference_number,
            due_date=job.due_date,
        )

        job.translation_job_uid = job_data["translationJobUid"]
        job.status = job_data["jobStatus"]

        now = timezone.now()
        job.first_synced_at = now
        job.last_synced_at = now

        job.full_clean()
        # Recorded before the first upload: a later run that asked Smartling
        # for a second job would be turned away for the duplicate name, and
        # the order would need repairing by hand in Smartling.
        job.save(update_fields=["translation_job_uid", "status", "first_synced_at", "last_synced_at"])

        if job.status == JobStatus.AWAITING_AUTHORIZATION:
            # Announced as soon as the job exists in Smartling. Everything
            # below can fail, and the run that picks the job up afterwards
            # finds the uid already recorded and announces nothing.
            job_synced_to_smartling.send(
                sender=job.__class__,
                instance=job,
            )

    # Uploading a file reads its source, its job and that job's project, which
    # is three queries per file of an order without the select_related.
    pending_files = list(job.files.filter(file_uri="").select_related("translation_source", "job__project"))
    if pending_files:
        _upload_pending_files(job, pending_files)
        if job.files.filter(file_uri="").exists():
            # Context is uploaded a file at a time and describes the whole job,
            # so it waits for the sweep that finishes sending the files.
            return

    # Add context to the job (if settings.VISUAL_CONTEXT_CALLBACK is defined)
    try:
        client.add_html_context_to_job(job=job)
    except Exception:
        # Every file is already at Smartling and authorized. Log the exception
        # here, but do not raise an error for it.
        logger.exception("Could not add visual context to job %s", job)


def _upload_pending_files(job: "Job", pending_files: list["JobFile"]) -> None:
    """
    Send the files the job still owes Smartling, in a batch.

    Files go in a batch so that associating them with their job cannot race;
    even a single PO file goes in one. Smartling does not promise to process
    two batches of the same job in parallel and refuses a file URI a batch
    already holds, so a push that stopped part-way resumes the batch it opened,
    instead of opening another. Only the files that are still missing are sent.
    """
    if job.batch_uid:
        try:
            batch = client.get_batch_status(job=job, batch_uid=job.batch_uid)
        except SmartlingBatchNotFound:
            logger.info("Batch %s of job %s is gone from Smartling", job.batch_uid, job)
            _record_batch_uid(job, "")
        else:
            if batch["status"] == BATCH_EXECUTING:
                # An executing batch takes no more files; a parallel batch would
                # not be processed reliably.
                logger.info("Batch %s of job %s is executing, so its files wait", job.batch_uid, job)
                return

            pending_files = _reconcile_pending_files_with_batch(job, batch, pending_files)
            if not pending_files:
                return
            if batch["status"] != BATCH_COMPLETED:
                _upload_files_to_batch(job, pending_files, batch_uid=job.batch_uid)
                return
            # If we have reached this point, the batch is completed, so it
            # takes no more files. We reset the batch uid, so another batch
            # can send the files that are left.
            _record_batch_uid(job, "")

    batch_uid = client.create_batch_for_job(job=job, job_files=pending_files)
    # Recorded before the first upload. If we do not store a batch uid, the
    # batch can not be finished, and it holds file URIs that no other batch
    # of this job can take.
    _record_batch_uid(job, batch_uid)
    _upload_files_to_batch(job, pending_files, batch_uid=batch_uid)


def _reconcile_pending_files_with_batch(
    job: "Job", batch: GetBatchStatusResponseData, pending_files: list["JobFile"]
) -> list["JobFile"]:
    """
    Record the files the batch already has, and return those still to be sent.

    A file the batch is still reading is left out of both: the next sweep picks
    it up. Raise BatchFileUnusable for one the batch has and cannot deliver.
    """
    status_by_file_uri = {item["fileUri"]: item["status"] for item in batch["files"]}
    files_to_send = []

    for job_file in pending_files:
        file_uri = client.get_file_uri_for_job_file(job_file=job_file)
        status = status_by_file_uri.get(file_uri)

        if status in BATCH_ITEM_STATUSES_THAT_LANDED:
            # It reached Smartling and our record of that was lost.
            _record_file_uri(job, job_file, file_uri)
        elif batch["status"] == BATCH_COMPLETED:
            # Nothing a finished batch holds will progress any further, so
            # whatever it did not deliver needs a batch of its own.
            files_to_send.append(job_file)
        elif status is None:
            # Declared when the batch was created, but never sent.
            files_to_send.append(job_file)
        elif status == BATCH_ITEM_FAILED:
            # Smartling has the bytes, cannot read them, and will not take them
            # again. Sending the same file to a new batch would only hide that.
            raise BatchFileUnusable(f"Smartling could not accept {file_uri} in batch {job.batch_uid}")
        elif status == BATCH_ITEM_CANCELED:
            # The batch can now finish, and the file goes into the batch once
            # it has.
            logger.info("Batch %s of job %s was told not to expect %s", job.batch_uid, job, file_uri)
        else:
            # Still in flight, under a name this version may not know: either
            # way the batch has the bytes and would refuse them a second time.
            logger.info("Batch %s of job %s is still handling %s (%s)", job.batch_uid, job, file_uri, status)

    return files_to_send


def _upload_files_to_batch(job: "Job", job_files: list["JobFile"], *, batch_uid: str) -> None:
    for job_file in job_files:
        file_uri = client.upload_file_to_job_batch(job_file=job_file, batch_uid=batch_uid)
        _record_file_uri(job, job_file, file_uri)


def _record_batch_uid(job: "Job", batch_uid: str) -> None:
    job.batch_uid = batch_uid
    job.save(update_fields=["batch_uid"])


def _record_file_uri(job: "Job", job_file: "JobFile", file_uri: str) -> None:
    """
    Mark the file as Smartling's, on the file and on the job together.

    The file's copy is what marks it uploaded. The job's copy is the deprecated
    single-file field, which takes the URI of the first file to land and goes
    when the field does.
    """
    with transaction.atomic():
        job_file.file_uri = file_uri
        job_file.save(update_fields=["file_uri"])
        if not job.file_uri:
            job.file_uri = file_uri
            job.save(update_fields=["file_uri"])


def _sync(job: "Job") -> None:
    """
    If the job has been synced to Smartling before, get its status and take the
    appropriate action if anything has changed.
    """
    if job.status == JobStatus.UNSYNCED:
        raise ValueError("call _initial_sync before calling _sync")

    logger.info("Syncing job %s", job)

    initial_status = job.status
    logger.info("Initial status: %s", initial_status)

    try:
        job_data = client.get_job_details(job=job)
    except SmartlingJobNotFound:
        logger.warning("Job not found in Smartling, marking as deleted")
        updated_status = JobStatus.DELETED
    else:
        updated_status = job_data["jobStatus"]
        job.description = job_data["description"]
        job.reference_number = job_data["referenceNumber"] or ""
        job.due_date = job_data["dueDate"]

    job.status = updated_status
    job.last_synced_at = timezone.now()

    # Check and import completed locales while job is still in progress
    # This allows individual locales to be imported before the full job completes
    if updated_status == JobStatus.IN_PROGRESS:
        imported = _check_and_import_completed_locales(job)
        if imported:
            translation_import_successful.send(
                sender=job.__class__,
                instance=job,
                translations_imported=imported,
            )

    if updated_status == initial_status:
        logger.info("No change in status, no further action required")
    else:
        logger.info("Updated status: %s", updated_status)

        if initial_status in PENDING_STATUSES:
            if updated_status in PENDING_STATUSES:
                logger.info("Job still pending, no further action required")
            elif updated_status in TRANSLATED_STATUSES:
                _download_and_apply_translations(job)
                job.translations_imported_at = job.last_synced_at
            elif updated_status in UNTRANSLATED_STATUSES:
                logger.warning("Job is finalised but not translated")
        else:
            logger.info("Job already finalised, no further action required")

    # Written once the work that goes with the new status has been done.
    job.save(
        update_fields=[
            "status",
            "description",
            "reference_number",
            "due_date",
            "last_synced_at",
            "translations_imported_at",
        ]
    )


def _check_and_import_completed_locales(job: "Job") -> list[Translation]:
    """
    Import every pending translation whose file Smartling reports as fully
    translated in that translation's locale.

    A locale finishes at different times for different files of the same job,
    and each one is imported as soon as its own file is done.

    Returns a list of Translation objects that were imported.
    """

    imported_translations: list[Translation] = []

    # Get all JobTranslation records that haven't been imported yet
    pending_job_translations: list[JobTranslation] = list(
        job.job_translations.filter(imported_at__isnull=True).select_related(  # pyright: ignore[reportAttributeAccessIssue]
            "translation__target_locale", "translation__source"
        )
    )

    if not pending_job_translations:
        logger.info("No pending translations to check for job %s", job)
        return imported_translations

    # Keyed by source because a file's locales are separate translations of it.
    job_files_by_source = {job_file.translation_source_id: job_file for job_file in job.files.all()}
    now = timezone.now()

    for job_translation in pending_job_translations:
        translation = job_translation.translation
        job_file = job_files_by_source.get(translation.source_id)
        if job_file is None:
            logger.warning(
                "Translation %s belongs to no file of job %s, skipping",
                translation.pk,
                job,
            )
            continue

        if not job_file.file_uri:
            logger.warning(
                "%s never reached Smartling, so translation %s cannot be checked yet",
                job_file,
                translation.pk,
            )
            continue

        smartling_locale_id = utils.format_smartling_locale_id(translation.target_locale.language_code)

        try:
            file_status = client.get_file_status_for_locale(job_file=job_file, locale_id=smartling_locale_id)
        except Exception:
            logger.exception(
                "Could not get the status of %s in locale %s, skipping",
                job_file,
                smartling_locale_id,
            )
            continue

        total_strings = file_status["totalStringCount"]
        completed_strings = file_status["completedStringCount"]

        logger.info(
            "%s in locale %s: %d/%d strings completed",
            job_file,
            smartling_locale_id,
            completed_strings,
            total_strings,
        )

        # Only import if 100% complete
        if total_strings > 0 and completed_strings >= total_strings:
            try:
                # Make sure we record the imported time and the content..
                with transaction.atomic():
                    content_hash = _import_translation_for_locale(job, job_file, translation, smartling_locale_id)
                    job_translation.imported_at = now
                    job_translation.content_hash = content_hash
                    job_translation.save(update_fields=["imported_at", "content_hash"])
                imported_translations.append(translation)
                logger.info(
                    "Imported translation for locale %s (job %s)",
                    smartling_locale_id,
                    job,
                )
            except Exception:
                logger.exception("Error importing translation for locale %s", smartling_locale_id)

    return imported_translations


def _import_translation_for_locale(
    job: "Job", job_file: "JobFile", translation: Translation, smartling_locale_id: str
) -> str:
    """
    Download and import the translation for a single locale of a single file.

    Returns the content hash of the imported translation.
    """
    content = client.download_translation_for_locale(job_file=job_file, locale_id=smartling_locale_id)
    content_str = content.decode("utf-8")
    content_hash = _compute_translation_hash(content_str)
    po_file = polib.pofile(_sanitize_po_content(content_str))
    translation.import_po(po_file)
    individual_translation_imported.send(
        sender=job.__class__,
        instance=job,
        translation=translation,
    )
    return content_hash


def _download_and_apply_translations(job: "Job") -> None:
    """
    Download every file of a Smartling job and apply the translations in it.

    One zip per file, each carrying all of that file's locales, and each entry
    imported into the page its file was uploaded from.
    """

    logger.info("Downloading and importing translations for job %s", job)

    _translations_imported = []
    now = timezone.now()

    # The endpoint takes one fileUri and answers with every locale of it, so a
    # job of many pages costs one zip per page rather than one per page and
    # locale.
    # A file with no URI never reached Smartling, so there is nothing there to
    # ask for; its page is translated once the rest of the push goes out.
    job_files_by_uri = {
        job_file.file_uri: job_file for job_file in job.files.select_related("translation_source") if job_file.file_uri
    }
    unsent_file_count = job.files.filter(file_uri="").count()
    if unsent_file_count:
        logger.warning("Job %s has %d file(s) that never reached Smartling", job, unsent_file_count)
    unknown_file_uris = []

    for job_file in job_files_by_uri.values():
        with client.download_translations(job_file=job_file) as translations_zip:
            for zipinfo in translations_zip.infolist():
                # Filenames are of the format "{localeId}/{fileUri}"
                smartling_locale_id, file_uri = zipinfo.filename.split("/")

                entry_file = job_files_by_uri.get(file_uri)
                if entry_file is None:
                    # Raised once the loop is over. Stopping here would leave
                    # every page after this entry unimported, and a stray entry
                    # is deterministic, so no later run would get past it.
                    unknown_file_uris.append(file_uri)
                    continue

                wagtail_locale_id = utils.format_wagtail_locale_id(smartling_locale_id)
                try:
                    # Found by file as well as by locale: a locale on its own
                    # cannot tell one page's translation from another's, and
                    # pages in one order can be ordered in different locales.
                    translation: Translation = job.translations.get(
                        source=entry_file.translation_source,
                        target_locale__language_code=wagtail_locale_id,
                    )
                except job.translations.model.DoesNotExist:
                    logger.info(
                        "Translation not found for %s in locale %s, skipping",
                        entry_file,
                        wagtail_locale_id,
                    )
                    continue

                job_translation = job.job_translations.filter(  # pyright: ignore[reportAttributeAccessIssue]
                    translation=translation
                ).first()

                with translations_zip.open(zipinfo) as f:
                    content = f.read().decode("utf-8")
                    content_hash = _compute_translation_hash(content)

                    # Skip if already imported with the same content hash
                    if job_translation and job_translation.imported_at and job_translation.content_hash == content_hash:
                        logger.info(
                            "%s in locale %s already imported with same content, skipping",
                            entry_file,
                            wagtail_locale_id,
                        )
                        continue

                    if job_translation and job_translation.imported_at:
                        logger.info(
                            "%s in locale %s changed since per-locale import, re-importing",
                            entry_file,
                            wagtail_locale_id,
                        )

                    # A locale half imported, or imported without its marker,
                    # would be worse than one this pass did not reach.
                    with transaction.atomic():
                        po_file = polib.pofile(_sanitize_po_content(content))
                        translation.import_po(po_file)
                        individual_translation_imported.send(
                            sender=job.__class__,
                            instance=job,
                            translation=translation,
                        )

                        # Mark as imported with content hash
                        if job_translation:
                            job_translation.imported_at = now
                            job_translation.content_hash = content_hash
                            job_translation.save(update_fields=["imported_at", "content_hash"])

                    logger.info("Imported translations for %s", translation)
                    _translations_imported.append(translation)

    if _translations_imported:
        translation_import_successful.send(
            sender=job.__class__,
            instance=job,
            translations_imported=_translations_imported,
        )

    if unknown_file_uris:
        raise FileURIMismatch(f"{', '.join(unknown_file_uris)} belong to no file of job {job.pk}")
