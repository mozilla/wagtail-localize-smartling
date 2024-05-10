from django import forms
from django.http import HttpRequest


class CallbackForm(forms.Form):
    pass


def create_job_callback_view(request: HttpRequest):
    pass
