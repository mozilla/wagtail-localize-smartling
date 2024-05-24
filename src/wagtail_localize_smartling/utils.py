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
