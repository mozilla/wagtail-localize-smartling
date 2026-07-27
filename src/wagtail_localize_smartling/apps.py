# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.apps import AppConfig


class WagtailLocalizeSmartlingAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    label = "wagtail_localize_smartling"
    name = "wagtail_localize_smartling"
    verbose_name = "Wagtail Localize Smartling"

    def ready(self):
        from . import checks, signal_handlers, signals  # noqa: F401
