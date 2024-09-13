from wagtail_localize_smartling.api.types import JobStatus


PENDING_STATUSES = (
    JobStatus.DRAFT,
    JobStatus.AWAITING_AUTHORIZATION,
    JobStatus.IN_PROGRESS,
)
TRANSLATED_STATUSES = (JobStatus.COMPLETED, JobStatus.CLOSED)
UNTRANSLATED_STATUSES = (JobStatus.CANCELLED, JobStatus.DELETED)
FINAL_STATUSES = (JobStatus.CLOSED, JobStatus.DELETED)
