from django.urls import path

from . import views


# TODO register these
app_name = "wagtail_localize_smartling"
urlpatterns = [
    path("callback/job/", views.job_callback, name="job_callback"),
    path("callback/file/", views.file_callback, name="file_callback"),
]
