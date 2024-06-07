import logging

from typing import Any, Dict

from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView
from wagtail.admin.views.generic import WagtailAdminTemplateMixin
from wagtail.models import Locale

from .models import Project
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
