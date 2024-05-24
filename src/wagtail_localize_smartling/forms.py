from typing import Any, Dict

from django.core.exceptions import ValidationError
from wagtail.admin.forms import WagtailAdminModelForm

from .api.client import client
from .utils import default_job_description, default_job_name


class JobForm(WagtailAdminModelForm):
    def __init__(self, *args, source_object_instance, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].initial = default_job_name(source_object_instance)
        self.fields["description"].initial = default_job_description(
            source_object_instance
        )
        self.user = user

    def clean_name(self):
        """
        Validate the job name by checking that no job with the same name already
        exists.
        """
        name = self.cleaned_data["name"]
        jobs_data = client.list_jobs(name=name)
        if any(j["jobName"] == name for j in jobs_data["items"]):
            raise ValidationError("A job with this name already exists")
        return name

    def clean(self) -> Dict[str, Any]:
        data = super().clean()
        data["user"] = self.user
        return data

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.user = self.user
        if commit:
            obj.save()
            self.save_m2m()
        return obj
