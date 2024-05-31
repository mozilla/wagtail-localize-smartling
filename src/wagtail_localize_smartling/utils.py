from urllib.parse import urljoin

from django.db import models
from wagtail.models import Site
from wagtail_localize.models import get_edit_url


def default_job_name(instance: models.Model) -> str:
    return str(instance)


def default_job_description(instance: models.Model) -> str:
    edit_url = ""
    try:
        default_site = Site.objects.get(is_default_site=True)
    except Site.DoesNotExist:
        pass
    else:
        edit_url = urljoin(default_site.root_url, get_edit_url(instance))

    description = f'Automatically-created Wagtail translation job for "{instance}"'
    if edit_url:
        description += f": {edit_url}"

    return description


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
