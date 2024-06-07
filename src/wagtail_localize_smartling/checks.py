from django.core.checks import Error, register
from django.core.exceptions import ImproperlyConfigured

from .settings import settings as smartling_settings


@register()
def smartling_settings_check(app_configs, **kwargs):
    errors = []
    try:
        str(smartling_settings.PROJECT_ID)
    except ImproperlyConfigured as e:
        errors.append(
            Error(
                e.args[0],
                id="wagtail_localize_smartling.E001",
            )
        )
    return errors
