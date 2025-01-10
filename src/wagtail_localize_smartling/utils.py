import hashlib

from typing import TYPE_CHECKING
from urllib.parse import quote, urljoin, urlparse

from django.urls import reverse
from wagtail.coreutils import (
    get_content_languages,
    get_supported_content_language_variant,
)
from wagtail.models import Locale
from wagtail_localize.models import LocaleSynchronization

from .api.types import JobStatus
from .settings import settings as smartling_settings


if TYPE_CHECKING:
    from polib import POFile

    from .models import Job, Project


def format_smartling_locale_id(locale_id: str) -> str:
    """
    Format a locale ID for the Smartling API. Wagtail/Django use lower case
    (e.g. "en-us") whereas Smartling uses lower case for the language code and
    upper case for the region, if any (e.g. "en-US").

    Also, this applies any mapping defined by the LOCALE_MAPPING_CALLBACK or
    LOCALE_TO_SMARTLING_LOCALE settings.
    """
    # Apply any mapping defined in settings
    locale_id = smartling_settings.LOCALE_TO_SMARTLING_LOCALE.get(locale_id, locale_id)

    # Reformat to match Smartling's format/casing
    original_parts = locale_id.split("-")
    if len(original_parts) == 1:
        return original_parts[0].lower()
    elif len(original_parts) == 2:
        return f"{original_parts[0].lower()}-{original_parts[1].upper()}"

    raise ValueError("Invalid locale ID")


def format_wagtail_locale_id(locale_id: str) -> str:
    """
    The opposite of format_smartling_locale_id.

    Returns everything lower case unless the REFORMAT_LANGUAGE_CODES setting is
    False.
    """
    # Apply any mapping defined in settings
    locale_id = smartling_settings.SMARTLING_LOCALE_TO_LOCALE.get(locale_id, locale_id)

    if not smartling_settings.REFORMAT_LANGUAGE_CODES:
        return locale_id

    return locale_id.lower()


def _get_smartling_dashboard_base_url() -> str:
    if smartling_settings.ENVIRONMENT == "staging":
        return "https://dashboard.stg.smartling.net"
    return "https://dashboard.smartling.com"


def format_smartling_project_url(project: "Project") -> str:
    pid = quote(project.project_id)
    return urljoin(
        _get_smartling_dashboard_base_url(),
        f"/app/projects/{pid}",
    )


def format_smartling_job_url(job: "Job") -> str:
    if job.status in (JobStatus.UNSYNCED, JobStatus.DELETED):
        # Can't generate a link for a job that doesn't exist in Smartling
        return ""

    pid = quote(job.project.project_id)
    jid = quote(job.translation_job_uid)
    return urljoin(
        _get_smartling_dashboard_base_url(),
        f"/app/projects/{pid}/account-jobs/{pid}:{jid}",
    )


# TODO test
def get_wagtail_source_locale(project: "Project") -> Locale | None:
    """
    Returns the Wagtail Locale that is compatible with the Smartling project's
    source locale if one exists, None otherwise.

    The Smartling source locale is compatible if a matching Locale exists in the
    database and that locale isn't syncing from any other Locale.
    """
    from .models import Project

    project = Project.get_current()

    # TODO factor in LANGUAGE_CODE

    try:
        locale = Locale.objects.get_for_language(project.source_locale_id)  # pyright: ignore[reportAttributeAccessIssue]
    except Locale.DoesNotExist:
        return None

    if LocaleSynchronization.objects.filter(locale=locale).exists():
        return None

    return locale


def suggest_source_locale(project: "Project") -> tuple[str, str] | None:
    """
    Return a tuple of language code and label for a suggested Locale from
    WAGTAIL_CONTENT_LANGUAGES that would match the Smartling project's source
    locale. The Locale doesn't have to exist, it just needs to be creatable. If
    no compatible locale is found, return None.
    """
    from .models import Project

    project = Project.get_current()
    try:
        language_code = get_supported_content_language_variant(project.source_locale_id)
    except LookupError:
        return None

    content_languages = get_content_languages()
    return (language_code, content_languages[language_code])


def compute_content_hash(pofile: "POFile") -> str:
    """
    Generates a hash from a generated PO file. We use this to reliably
    check the translation source content before creating a Smartling Job
    """
    strings = []
    for entry in pofile:
        strings.append(f"{entry.msgctxt}: {entry.msgid}")

    return hashlib.sha256("".join(strings).encode()).hexdigest()


def get_filename_for_visual_context(url: str, max_length: int = 256) -> str:
    """
    Turn the given url into a long sluglike HTML filename, based
    on the hostname and the path
    """
    if not url:
        return url

    _parsed = urlparse(url)

    _hostname = _parsed.hostname or ""
    _path = _parsed.path or ""

    head = "-".join(_hostname.split(".")).rstrip("-").lower()
    tail = "-".join(_path.split("/")).rstrip("-").lower()
    body = f"{head}{tail}"[: max_length - 5]

    return f"{body}.html"


def get_snippet_admin_url(snippet):
    # Crude, but simple and effective
    app_label = snippet._meta.app_label
    model_name = snippet._meta.model_name
    admin_root_path = reverse("wagtailadmin_home")

    return f"{admin_root_path}snippets/{app_label}/{model_name}/edit/{snippet.pk}/"
