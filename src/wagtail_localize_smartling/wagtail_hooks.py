from django.urls import include, path
from django.views.i18n import JavaScriptCatalog
from wagtail import hooks


# TODO


@hooks.register("register_admin_urls")  # pyright: ignore[reportOptionalCall]
def register_admin_urls():
    urls = [
        path(
            "jsi18n/",
            JavaScriptCatalog.as_view(packages=["wagtail_localize_smartling"]),
            name="javascript_catalog",
        ),
        # Add your other URLs here, and they will appear under
        # `/admin/localize_smartling/`
        #
        # Note: you do not need to check for authentication in views added here,
        # Wagtail does this for you!
    ]

    return [
        path(
            "localize_smartling/",
            include(
                (urls, "wagtail_localize_smartling"),
                namespace="wagtail_localize_smartling",
            ),
        )
    ]
