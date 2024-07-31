from django.urls import include, path, reverse
from django.utils.translation import gettext as _
from wagtail import hooks
from wagtail.admin.menu import MenuItem

from . import admin_urls
from .viewsets import smartling_job_viewset


@hooks.register("register_admin_urls")  # pyright: ignore[reportOptionalCall]
def register_admin_urls():
    return [path("smartling/", include(admin_urls))]


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
