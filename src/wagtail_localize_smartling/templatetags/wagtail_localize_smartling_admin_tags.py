from typing import Any

from django import template
from django.urls import reverse
from django.utils.translation import gettext as _
from wagtail.admin import messages as admin_messages
from wagtail.admin.utils import set_query_params

from wagtail_localize_smartling.constants import UNTRANSLATED_STATUSES
from wagtail_localize_smartling.models import Job
from wagtail_localize_smartling.utils import format_smartling_job_url


register = template.Library()


# TODO test
@register.inclusion_tag(
    "wagtail_localize_smartling/admin/edit_translation_message.html",
    takes_context=True,
)
def smartling_edit_translation_message(context):
    inclusion_context: dict[str, Any] = {
        "show_message": False,
    }

    translation = context["translation"]
    jobs = translation.smartling_jobs.all()

    jobs_exists = bool(jobs)
    inclusion_context["show_message"] = jobs_exists

    if not jobs_exists:
        return inclusion_context

    # Jobs are ordered by `first_synced_at`, with null values coming at the top
    # then pk descending. So we choose the first in the list. This may mean the job
    # status will show as unsynced in the message below
    latest_job = list(jobs)[0]
    buttons = []

    if latest_job.status in UNTRANSLATED_STATUSES:
        message = _(
            "The latest Smartling job for this translation "
            f"was {latest_job.get_status_display().lower()}."
        )
        resubmit_url = set_query_params(
            reverse("wagtail_localize_smartling_retry_job", args=(latest_job.pk,)),
            {"next": context["request"].path},
        )
        buttons.append(
            admin_messages.button(
                resubmit_url,
                _("Resubmit to Smartling"),
                new_window=True,
            ),
        )
    else:
        message = _(
            "This translation is managed by Smartling. Changes made here will be lost "
            "the next time translations are imported from Smartling. "
            f"Job status: {latest_job.get_status_display()}"
        )
        if smartling_url := format_smartling_job_url(latest_job):
            buttons.append(
                admin_messages.button(
                    smartling_url,
                    _("View job in Smartling"),
                    new_window=True,
                ),
            )

    inclusion_context["message"] = admin_messages.render(
        message,
        buttons=buttons,
    )

    return inclusion_context


@register.simple_tag
def smartling_job_url(job: Job) -> str:
    return format_smartling_job_url(job)
