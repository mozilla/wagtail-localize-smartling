from wagtail_localize_smartling.api.types import JobStatus


PENDING_STATUSES = (
    JobStatus.DRAFT,
    JobStatus.AWAITING_AUTHORIZATION,
    JobStatus.IN_PROGRESS,
)
UNSYNCED_OR_PENDING_STATUSES = PENDING_STATUSES + (JobStatus.UNSYNCED,)
TRANSLATED_STATUSES = (JobStatus.COMPLETED, JobStatus.CLOSED)
UNTRANSLATED_STATUSES = (JobStatus.CANCELLED, JobStatus.DELETED)
FINAL_STATUSES = (JobStatus.CLOSED, JobStatus.DELETED)

# A job in one of these is not with translators yet: either nothing has been
# pushed, or Smartling holds it and nobody has released it.
AWAITING_AUTHORIZATION_STATUSES = (
    JobStatus.UNSYNCED,
    JobStatus.DRAFT,
    JobStatus.AWAITING_AUTHORIZATION,
)

# Job statuses where we can add new locales to an existing job
EXPANDABLE_JOB_STATUSES = (
    JobStatus.UNSYNCED,
    JobStatus.DRAFT,
    JobStatus.AWAITING_AUTHORIZATION,
)
