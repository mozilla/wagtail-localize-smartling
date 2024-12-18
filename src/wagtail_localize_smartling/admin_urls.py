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
