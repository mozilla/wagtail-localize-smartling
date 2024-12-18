import logging

from typing import Any

from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView
from laces.components import MediaContainer
from wagtail.admin.auth import permission_denied
from wagtail.admin.utils import get_valid_next_url_from_request
from wagtail.admin.views.generic import (
    PermissionCheckedMixin,
    WagtailAdminTemplateMixin,
)
from wagtail.models import Locale
from wagtail.permission_policies import ModelPermissionPolicy

from .components import LandedTranslationsPanel
from .constants import UNTRANSLATED_STATUSES
from .models import Job, Project
from .templatetags.wagtail_localize_smartling_admin_tags import smartling_job_url
from .utils import (
    format_smartling_project_url,
    get_wagtail_source_locale,
    suggest_source_locale,
)


logger = logging.getLogger(__name__)


class SmartlingStatusView(WagtailAdminTemplateMixin, TemplateView):  # pyright: ignore[reportIncompatibleMethodOverride]
    _show_breadcrumbs = True
    page_title = _("Smartling")
    template_name = "wagtail_localize_smartling/admin/smartling_status.html"

    def get_breadcrumbs_items(self):
        return super().get_breadcrumbs_items() + [{"url": "", "label": _("Smartling")}]

    def get_context_data(self, **kwargs):
        try:
            project = Project.get_current()
        except Exception:
            logger.exception("Failed to get current Smartling project")
            project = None

        context: dict[str, Any] = {
            "project": project,
        }

        if project:
            # Link to the project in the Smartling dashboard
            context["project_url"] = format_smartling_project_url(project)

            # List the target locales of the Smartling project
            context["target_locales"] = [
                {
                    "description": tl.description,
                    "enabled": tl.enabled,
                }
                for tl in project.target_locales.all()
            ]

            # Source locale information
            context["wagtail_source_locale"] = get_wagtail_source_locale(project)
            suggested = suggest_source_locale(project)
            if suggested is not None:
                try:
                    locale = Locale.objects.get(language_code=suggested[0])
                except Locale.DoesNotExist:
                    locale = None
                context["suggested_source_locale_exists"] = locale is not None
                context["suggested_source_locale"] = {
                    "language_code": suggested[0],
                    "label": suggested[1],
                }
                if locale is not None:
                    context["suggested_source_locale"]["url"] = reverse(
                        "wagtaillocales:edit", kwargs={"pk": locale.pk}
                    )

            else:
                context["suggested_source_locale"] = None
                context["suggested_source_locale_exists"] = False

        return super().get_context_data(**context)


class SmartlingResubmitJobView(  # pyright: ignore[reportIncompatibleMethodOverride]
    PermissionCheckedMixin, WagtailAdminTemplateMixin, TemplateView
):
    _show_breadcrumbs = True
    page_title = _("Resubmit to Smartling")
    template_name = "wagtail_localize_smartling/admin/resubmit_job.html"
    header_icon = "wagtail-localize-language"
    object: Job
    permission_policy = ModelPermissionPolicy(Job)
    permission_required = "view"
    jobs_report_url: str = ""

    def get_breadcrumbs_items(self):
        # TODO: link to the report view
        return super().get_breadcrumbs_items() + [
            {"url": self.jobs_report_url, "label": _("Smartling jobs")},
            {"url": "", "label": self.page_title},
        ]

    def get_object(self):
        return get_object_or_404(Job, pk=self.kwargs.get("job_id"))

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        if self.object.status not in UNTRANSLATED_STATUSES:
            return permission_denied(request)

        # Check that the given Job is the latest. We can use any of its translations
        translation = self.object.translations.first()
        if translation is None:
            # should cover the case where the translation was converted back to an alias
            return permission_denied(request)

        # Jobs are ordered by `first_synced_at`, with null values coming at the top
        # then pk descending.
        latest_job = translation.smartling_jobs.values_list("pk", flat=True)[0]  # pyright: ignore[reportAttributeAccessIssue]
        if latest_job != self.object.pk:
            return permission_denied(request)

        self.jobs_report_url = reverse("wagtail_localize_smartling_jobs:index")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        context = self.get_context_data(object=self.object)
        return self.render_to_response(context)

    def post(self, request, *args, **kwargs):
        if self.object:
            due_date = self.object.due_date
            Job.get_or_create_from_source_and_translation_data(
                self.object.translation_source,
                self.object.translations.all(),
                user=request.user,
                due_date=due_date if due_date and due_date >= timezone.now() else None,
            )

        return redirect(self.get_success_url())

    def get_success_url(self):
        return get_valid_next_url_from_request(self.request) or self.jobs_report_url

    @property
    def confirmation_message(self):
        return _("Are you sure you want to resubmit this job?")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["view"] = self
        if smartling_url := smartling_job_url(self.object):
            context["view_url"] = format_html(
                '<a href="{}" title="Reference {}" target="_blank">{}</a>',
                smartling_url,
                self.object.reference_number,
                _("View job in Smartling"),
            )

        return context


def landed_translations_list(request):
    components = MediaContainer(
        [
            LandedTranslationsPanel(),
        ]
    )

    return render(
        request,
        "wagtail_localize_smartling/admin/landed_translation_tasks.html",
        {
            "components": components,
        },
    )
