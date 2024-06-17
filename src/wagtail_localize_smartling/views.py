import logging

from typing import Any, Dict

import django_filters

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView
from wagtail.admin.filters import WagtailFilterSet
from wagtail.admin.views.generic import WagtailAdminTemplateMixin
from wagtail.admin.views.reports import ReportView
from wagtail.models import Locale

from .api.types import JobStatus
from .models import Job, Project
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

        context: Dict[str, Any] = {
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


def get_users_for_filter(user):
    User = get_user_model()
    return User.objects.filter(
        pk__in=Job.objects.values_list("user", flat=True)
    ).order_by(User.USERNAME_FIELD)  # pyright: ignore[reportAttributeAccessIssue]


class JobReportFilterSet(WagtailFilterSet):
    status = django_filters.ChoiceFilter(choices=JobStatus.choices)
    user = django_filters.ModelChoiceFilter(
        label=_("User"),
        field_name="user",
        queryset=lambda request: get_users_for_filter(request.user),
    )

    class Meta:
        model = Job
        fields = ["status"]


class JobReportView(ReportView):
    title = _("Smartling jobs")
    template_name = "wagtail_localize_smartling/admin/job_report.html"
    filterset_class = JobReportFilterSet

    def get_queryset(self):
        return Job.objects.select_related("translation_source").prefetch_related(
            "translations__target_locale"
        )
