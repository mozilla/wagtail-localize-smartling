from django.apps import AppConfig


class WagtailLocalizeSmartlingAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    label = "wagtail_localize_smartling"
    name = "wagtail_localize_smartling"
    verbose_name = "Wagtail Localize Smartling"

    def ready(self):
        from . import checks, signal_handlers, signals  # noqa: F401
