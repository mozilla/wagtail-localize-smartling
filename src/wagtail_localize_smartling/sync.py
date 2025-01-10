import logging

from typing import TYPE_CHECKING

import polib

from django.db import transaction
from django.utils import timezone
from wagtail_localize.models import Translation

from . import utils
from .api.client import client
from .api.types import JobStatus
from .constants import PENDING_STATUSES, TRANSLATED_STATUSES, UNTRANSLATED_STATUSES
from .signals import individual_translation_imported, translation_import_successful


if TYPE_CHECKING:
    from .models import Job


logger = logging.getLogger(__name__)


class SyncJobException(Exception):
    pass


class JobNotFound(SyncJobException):
    pass


class FileURIMismatch(SyncJobException):
    pass


@transaction.atomic(durable=True)
def sync_job(job_id: int) -> None:
    """
    Sync the state of a Job instance with the corresponding job in Smartling.

    This uses select_for_update() to lock the Job row for the duration of the
    method. These locks are only released when the _transaction_ ends, not
    savepoints. To ensure we don't accidentally create long-lasting locks on the
    the Job rows, we pass durable=True to the atomic decorator. That means that
    this atomic() block is definitely the outermost one and the transaction will
    be committed or rolled back at the end of this function.

    NB - this takes an ID, rather than a job instance, so that it's always
    operating on current data rather than pickled state. That means it's safe
    to be called after arbitrary time from the background task queue, if one
    is in use.
    """
    from .models import Job

    try:
        job = Job.objects.select_for_update().get(pk=job_id)
    except Job.DoesNotExist as e:
        raise JobNotFound(f"Job with ID {job_id} not found") from e

    try:
        if job.status == JobStatus.UNSYNCED:
            _initial_sync(job)
        else:
            _sync(job)
    except Exception as e:
        raise SyncJobException(f"Exception syncing job {job}") from e


def _initial_sync(job: "Job") -> None:
    """
    For jobs that have never been synced before, create the job in Smartling and
    add the PO file from the TranslationSource.

    Also add Visual Context for Smartling CAT, if a callback to get that is configured
    """
    logger.info("Performing initial sync for job %s", job)

    # Create the job in the Smartling API
    target_locale_ids = [
        utils.format_smartling_locale_id(lc)
        for lc in job.translations.values_list(
            "target_locale__language_code", flat=True
        )
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
    job.save()

    # Create a Job Batch so we can upload Files without race conditions
    # in associating them with a Job (even a single PO file can go in a batch)
    batch_uid = client.create_batch_for_job(job=job)

    # Upload the TranslationSource's PO file to the Batch in Smartling
    file_uri = client.upload_files_to_job_batch(
        job=job,
        batch_uid=batch_uid,
    )

    # Add the file URI to the Job
    job.file_uri = file_uri
    job.save(update_fields=["file_uri"])

    # Add context to the job (if settings.VISUAL_CONTEXT_CALLBACK is defined)
    client.add_html_context_to_job(job=job)


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
    except JobNotFound:
        logger.warning("Job not found in Smartling, marking as deleted")
        updated_status = JobStatus.DELETED
    else:
        updated_status = job_data["jobStatus"]
        job.description = job_data["description"]
        job.reference_number = job_data["referenceNumber"] or ""
        job.due_date = job_data["dueDate"]

    job.status = updated_status
    job.last_synced_at = timezone.now()
    job.save()

    if updated_status == initial_status:
        logger.info("No change in status, no further action required")
        return

    logger.info("Updated status: %s", updated_status)

    if initial_status in PENDING_STATUSES:
        if updated_status in PENDING_STATUSES:
            logger.info("Job still pending, no further action required")
        elif updated_status in TRANSLATED_STATUSES:
            _download_and_apply_translations(job)
            job.translations_imported_at = job.last_synced_at
            job.save(update_fields=["translations_imported_at"])
        elif updated_status in UNTRANSLATED_STATUSES:
            logger.warning("Job is finalised but not translated")
    else:
        logger.info("Job already finalised, no further action required")


def _download_and_apply_translations(job: "Job") -> None:
    """
    Download the translated files from a Smartling job and apply them
    """

    logger.info("Downloading and importing translations for job %s", job)

    _translations_imported = []

    with client.download_translations(job=job) as translations_zip:
        for zipinfo in translations_zip.infolist():
            # Filenames are of the format "{localeId}/{fileUri}"
            smartling_locale_id, file_uri = zipinfo.filename.split("/")

            if file_uri != job.file_uri:
                raise FileURIMismatch(
                    f"File URI mismatch: expected {job.file_uri}, got {file_uri}"
                )

            wagtail_locale_id = utils.format_wagtail_locale_id(smartling_locale_id)
            try:
                translation: Translation = job.translations.get(
                    target_locale__language_code=wagtail_locale_id
                )
            except job.translations.model.DoesNotExist:
                logger.info(
                    "Translation not found for locale %s, skipping", wagtail_locale_id
                )
                continue

            with translations_zip.open(zipinfo) as f:
                po_file = polib.pofile(f.read().decode("utf-8"))
                translation.import_po(po_file)
                individual_translation_imported.send(
                    sender=job.__class__,
                    instance=job,
                    translation=translation,
                )
                logger.info("Imported translations for %s", translation)
                _translations_imported.append(translation)

    if _translations_imported:
        translation_import_successful.send(
            sender=job.__class__,
            instance=job,
            translations_imported=_translations_imported,
        )
