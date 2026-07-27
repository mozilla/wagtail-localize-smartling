# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

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
