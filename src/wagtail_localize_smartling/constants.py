# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

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

# Job statuses where we can add new locales to an existing job
EXPANDABLE_JOB_STATUSES = (
    JobStatus.UNSYNCED,
    JobStatus.DRAFT,
    JobStatus.AWAITING_AUTHORIZATION,
)
