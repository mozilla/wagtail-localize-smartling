"""
One row per Smartling job an order has created.

An order that was cancelled, or that failed on its way out, can be sent again,
so the record of what it did is a list rather than a single set of fields.
"""

import dataclasses

from typing import TYPE_CHECKING

from django.db.models import Count
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from .api.types import JobStatus
from .constants import UNTRANSLATED_STATUSES
from .utils import format_smartling_job_url


if TYPE_CHECKING:
    from .models import Job, Order


@dataclasses.dataclass
class SendRow:
    job: "Job"
    send_number: int
    file_count: int
    locale_count: int
    can_be_resubmitted: bool

    @property
    def blocked_before_sending(self) -> bool:
        """Whether this send gave up before Smartling had the files."""
        return self.job.sync_blocked and not self.job.translation_job_uid

    @property
    def status_label(self) -> str:
        if self.blocked_before_sending:
            return _("Submission failed")
        if self.job.status == JobStatus.UNSYNCED:
            # The stored value is "Unsynced", which names the field rather than
            # telling the editor where the pages have got to.
            return _("Not yet sent to Smartling")
        return self.job.get_status_display()

    @property
    def error_message(self) -> str:
        return self.job.last_sync_error if self.blocked_before_sending else ""

    @property
    def failed_at(self):
        """When the send gave up, which is the only timestamp such a send has."""
        return self.job.last_sync_error_at if self.blocked_before_sending else None

    @property
    def smartling_url(self) -> str:
        return format_smartling_job_url(self.job)

    @property
    def resubmit_url(self) -> str:
        if not self.can_be_resubmitted:
            return ""
        return reverse("wagtail_localize_smartling_retry_job", args=(self.job.pk,))


def build_send_history(order: "Order") -> list[SendRow]:
    """Every send of this order, newest first."""
    jobs = list(
        order.jobs.select_related("project", "user")
        .annotate(
            # distinct, because the joins multiply each other's rows. The
            # locales are counted across the whole send.
            file_count=Count("files", distinct=True),
            locale_count=Count("job_translations__translation__target_locale", distinct=True),
        )
        # By pk, not Job.Meta.ordering, which puts a job that never reached
        # Smartling ahead of the one that replaced it.
        .order_by("pk")
    )
    rows = [
        SendRow(
            job=job,
            send_number=send_number,
            file_count=job.file_count,  # pyright: ignore[reportAttributeAccessIssue]
            locale_count=job.locale_count,  # pyright: ignore[reportAttributeAccessIssue]
            # Only the newest send, because an earlier one's locales are either
            # already landed or with Smartling under the send that replaced it,
            can_be_resubmitted=send_number == len(jobs) and job.status in UNTRANSLATED_STATUSES,
        )
        for send_number, job in enumerate(jobs, start=1)
    ]
    rows.reverse()
    return rows
