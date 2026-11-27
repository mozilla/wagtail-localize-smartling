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


@register.inclusion_tag(
    "wagtail_localize_smartling/admin/edit_translation_message.html",
    takes_context=True,
)
def smartling_edit_translation_message(context):
    inclusion_context: dict[str, Any] = {
        "show_message": False,
    }

    translation = context["translation"]
    # By pk, not Job.Meta.ordering, which could put a send that never reached
    # Smartling ahead of the one that replaced it.
    latest_job = translation.smartling_jobs.order_by("-pk").first()

    inclusion_context["show_message"] = latest_job is not None

    if latest_job is None:
        return inclusion_context

    buttons = []

    if latest_job.needs_a_new_job:
        if latest_job.status in UNTRANSLATED_STATUSES:
            message = _("The latest Smartling job for this translation was %(status)s.") % {
                "status": latest_job.get_status_display().lower()
            }
        else:
            # Blocked on its way out, so no status of Smartling's describes it.
            message = _("The latest Smartling job for this translation never reached Smartling.")

        # The same gate the resubmit view applies: it resends the whole order,
        # so a job the order has already replaced, or one whose locales have all
        # arrived since, has nothing left to offer.
        order = latest_job.order
        if order is not None and order.get_latest_job() == latest_job and latest_job.pending_translations().exists():
            resubmit_url = set_query_params(
                reverse("wagtail_localize_smartling_retry_job", args=(latest_job.pk,)),
                {"next": context["request"].path},
            )
            buttons.append(
                admin_messages.button(
                    resubmit_url,
                    _("Resubmit “%(order)s” to Smartling") % {"order": order.name},
                    new_window=True,
                ),
            )
    else:
        message = _(
            "This translation is managed by Smartling. Changes made here will be lost "
            "the next time translations are imported from Smartling. Job status: %(status)s"
        ) % {"status": latest_job.get_status_display()}
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
