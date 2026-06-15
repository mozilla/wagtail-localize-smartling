from django.urls import include, path, reverse
from django.utils.html import format_html
from django.utils.translation import gettext as _
from wagtail import hooks
from wagtail.admin.menu import MenuItem

from . import admin_urls
from .components import LandedTranslationsPanel
from .models import Job
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
    Pre-tick the "Send to Smartling" checkbox on the wagtail-localize
    submit/update-translation forms when the host project has opted in.

    wagtail-localize generates the checkbox as
    `<input type="checkbox" name="component-{Job._meta.db_table}-enabled">`
    with the initial state hardcoded to unchecked. Rather than patching
    upstream internals, we hook a tiny page-load script onto the admin
    that ticks the box client-side iff:

    - the host project has set `SEND_TO_SMARTLING_BY_DEFAULT` (or its
      DEV-derived default resolves to True), AND
    - the checkbox is currently unchecked, AND
    - there are no visible form errors on the page (so we don't override
      a user who deliberately unticked it on a re-render after a
      validation error elsewhere on the form).
    """
    if not smartling_settings.SEND_TO_SMARTLING_BY_DEFAULT:
        return ""

    name = f"component-{Job._meta.db_table}-enabled"
    return format_html(
        """
        <script>
        (function () {{
            function tickSendToSmartling() {{
                var cb = document.querySelector(
                    'input[type="checkbox"][name="{name}"]'
                );
                if (!cb || cb.checked) {{ return; }}
                if (document.querySelector('.error-message, .errorlist')) {{
                    return;
                }}
                cb.checked = true;
            }}
            if (document.readyState === 'loading') {{
                document.addEventListener('DOMContentLoaded', tickSendToSmartling);
            }} else {{
                tickSendToSmartling();
            }}
        }})();
        </script>
        """,
        name=name,
    )
