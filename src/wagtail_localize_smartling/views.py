import logging

from typing import Any

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
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
from .forms import ResubmitOrderForm
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

    def get_breadcrumbs_items(self):  # pyright: ignore[reportIncompatibleMethodOverride]
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
                    context["suggested_source_locale"]["url"] = reverse("wagtaillocales:edit", kwargs={"pk": locale.pk})

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
    orders_report_url: str = ""

    def get_breadcrumbs_items(self):  # pyright: ignore[reportIncompatibleMethodOverride]
        return super().get_breadcrumbs_items() + [
            {"url": self.orders_report_url, "label": _("Smartling orders")},
            {"url": "", "label": self.page_title},
        ]

    def get_object(self):
        return get_object_or_404(Job, pk=self.kwargs.get("job_id"))

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        if not self.object.needs_a_new_job:
            return permission_denied(request)

        # A job carries a whole order of pages, so the order is what gets
        # resent, and only its newest job may ask for that. An order sent again
        # after a cancellation leaves the dead job resubmittable for ever
        # otherwise, and pressing it would pay Smartling a second time for the
        # pages its replacement is translating right now.
        order = self.object.order
        if order is None or order.get_latest_job() != self.object:
            return permission_denied(request)

        self.orders_report_url = reverse("wagtail_localize_smartling_jobs:index")
        if not self.object.pending_translations().exists():
            # Every locale this send was carrying has arrived since, so a new
            # job would have nothing to put in its files.
            return self.nothing_left_to_send(request)

        return super().dispatch(request, *args, **kwargs)

    def get_form(self, data=None) -> ResubmitOrderForm:
        return ResubmitOrderForm(data, order=self.object.order)

    def get(self, request, *args, **kwargs):
        return self.render_to_response(self.get_context_data(form=self.get_form()))

    def post(self, request, *args, **kwargs):
        form = self.get_form(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))

        order = self.object.order
        try:
            order.submit(
                user=request.user,
                job_name=form.cleaned_data["job_name"],
                translations=self.object.pending_translations(),
            )
        except ValueError:
            # A locale stopped, or landed, between opening this screen and
            # confirming it.
            return self.nothing_left_to_send(request)
        except Exception:
            logger.exception("Could not resubmit order %s", order.pk)
            messages.error(
                request,
                _("'%(name)s' could not be sent to Smartling. Nothing was sent; try again.") % {"name": order.name},
            )
            return redirect(self.get_success_url())

        messages.success(request, _("'%(name)s' was resent to Smartling.") % {"name": order.name})

        if order.dropped_order_items:
            messages.info(
                request,
                ngettext(
                    "%(count)d page had already been translated and was not sent again: %(pages)s.",
                    "%(count)d pages had already been translated and were not sent again: %(pages)s.",
                    len(order.dropped_order_items),
                )
                % {"count": len(order.dropped_order_items), "pages": order.dropped_page_names},
            )

        return redirect(self.get_success_url())

    def get_success_url(self):
        return get_valid_next_url_from_request(self.request) or self.orders_report_url

    def nothing_left_to_send(self, request):
        messages.info(
            request,
            _("'%(name)s' has nothing left to send: every page in it has already been translated.")
            % {"name": self.object.order.name},
        )
        return redirect(self.get_success_url())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(object=self.object, **kwargs)
        context["view"] = self
        pending = self.object.pending_translations()
        context["order"] = self.object.order
        context["page_count"] = pending.values("source").distinct().count()
        context["locale_names"] = ", ".join(
            str(locale) for locale in Locale.objects.filter(pk__in=pending.values("target_locale"))
        )
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
