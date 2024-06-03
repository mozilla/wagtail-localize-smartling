from typing import TYPE_CHECKING
from urllib.parse import quote

from django.db import models
from wagtail.models import Site
from wagtail_localize.models import get_edit_url

from .api.types import JobStatus
from .settings import settings as smartling_settings


if TYPE_CHECKING:
    from .models import Job


def format_smartling_locale_id(locale_id: str) -> str:
    """
    Format a locale ID for the Smartling API. Wagtail/Django use lower case
    (e.g. "en-us") whereas Smartling uses lower case for the country code and
    upper case for the region, if any (e.g. "en-US").
    """
    original_parts = locale_id.split("-")
    if len(original_parts) == 1:
        return original_parts[0].lower()
    elif len(original_parts) == 2:
        return f"{original_parts[0].lower()}-{original_parts[1].upper()}"
    raise ValueError("Invalid locale ID")


def format_wagtail_locale_id(locale_id: str) -> str:
    """
    The opposite of format_smartling_locale_id, return everything lower case.
    """
    return locale_id.lower()


def format_smartling_job_url(job: "Job") -> str:
    print(job.status)
    if job.status in (JobStatus.UNSYNCED, JobStatus.DELETED):
        # Can't generate a link for a job that doesn't exist in Smartling
        return ""
    pid = quote(job.project.project_id)
    jid = quote(job.translation_job_uid)
    return (
        f"https://dashboard.smartling.com/app/projects/{pid}/account-jobs/{pid}:{jid}"
    )
