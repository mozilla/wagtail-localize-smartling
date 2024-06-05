from typing import Any, Dict

from django import template
from django.utils.translation import gettext as _
from wagtail.admin import messages as admin_messages

from ..utils import format_smartling_job_url


register = template.Library()


# TODO test
@register.inclusion_tag(
    "wagtail_localize_smartling/admin/edit_translation_message.html",
    takes_context=True,
)
def smartling_edit_translation_message(context):
    inclusion_context: Dict[str, Any] = {
        "show_message": False,
    }

    translation = context["translation"]
    jobs = translation.smartling_jobs.all()

    inclusion_context["show_message"] = bool(jobs)

    # TODO handle multiple jobs
    job = list(jobs)[-1]

    message = _(
        "This translation is managed by Smartling. Changes made here will be lost the "
        "next time translations are imported from Smartling."
    )
    buttons = []
    if smartling_url := format_smartling_job_url(job):
        buttons.append(
            admin_messages.button(
                smartling_url,
                "View Smartling job",
                new_window=True,
            ),
        )

    inclusion_context["message"] = admin_messages.render(
        message,
        buttons=buttons,
    )

    return inclusion_context
