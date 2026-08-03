from django.urls import include, path, reverse
from django.utils.html import format_html
from django.utils.translation import gettext as _
from wagtail import hooks
from wagtail.admin.menu import MenuItem
from wagtail.admin.staticfiles import versioned_static

from . import admin_urls
from .components import LandedTranslationsPanel
from .settings import settings as smartling_settings
from .views import SmartlingResubmitJobView
from .viewsets import smartling_job_viewset


@hooks.register("register_admin_urls")  # pyright: ignore[reportOptionalCall]
def register_admin_urls():
    return [
        path("smartling/", include(admin_urls)),
        path(
            "smartling-jobs/resubmit/<int:job_id>/",
            SmartlingResubmitJobView.as_view(),
            name="wagtail_localize_smartling_retry_job",
        ),
    ]


@hooks.register("register_settings_menu_item")  # pyright: ignore[reportOptionalCall]
def register_smartling_settings_menu_item():
    return MenuItem(
        _("Smartling"),
        reverse("wagtail_localize_smartling:status"),
        icon_name="wagtail-localize-language",
    )


@hooks.register("register_admin_viewset")  # pyright: ignore[reportOptionalCall]
def register_viewset():
    return smartling_job_viewset


@hooks.register("construct_homepage_panels")  # pyright: ignore[reportOptionalCall]
def add_landed_translations_panel(request, panels):
    _group_name = smartling_settings.TRANSLATION_APPROVER_GROUP_NAME
    if _group_name in request.user.groups.all().values_list("name", flat=True):
        panels.append(
            LandedTranslationsPanel(
                max_to_show=smartling_settings.MAX_APPROVAL_TASKS_ON_DASHBOARD
            )
        )


@hooks.register("insert_global_admin_js")  # pyright: ignore[reportOptionalCall]
def default_send_to_smartling_js():
    """
    Pre-tick the "Send to Smartling" checkbox on wagtail-localize's
    submit/update-translation forms when the host project has opted in
    via `SEND_TO_SMARTLING_BY_DEFAULT`.

    wagtail-localize hardcodes the checkbox's initial state to
    unchecked, with no parameter, hook, or settable attribute to
    override it. Rather than patching upstream internals at runtime,
    we ship a small static script that ticks the checkbox client-side.
    Loading via `<script src>` (rather than inline) keeps the script
    compatible with hosts running a `script-src 'self'` CSP without
    needing `'unsafe-inline'`.
    """
    if not smartling_settings.SEND_TO_SMARTLING_BY_DEFAULT:
        return ""

    return format_html(
        '<script src="{src}"></script>',
        src=versioned_static(
            "wagtail_localize_smartling/js/default-send-to-smartling.js"
        ),
    )
