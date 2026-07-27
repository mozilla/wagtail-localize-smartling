# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.urls import path

from . import views


app_name = "wagtail_localize_smartling"
urlpatterns = [
    path(
        "status/",
        views.SmartlingStatusView.as_view(),
        name="status",
    ),
    path(
        "landed-translations/",
        views.landed_translations_list,
        name="landed-translations",
    ),
]
