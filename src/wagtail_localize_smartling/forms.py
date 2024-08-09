from typing import Any, cast

from django.utils.translation import gettext_lazy as _
from wagtail.admin.forms import WagtailAdminModelForm


class JobForm(WagtailAdminModelForm):
    """
    Custom ModelForm for the Job model. We need to add the "user" key to the
    validated data _and_ set the attribute on the instance because of the way
    wagtail-localize's translation components work.
    """

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["due_date"].help_text = _(
            "Optional due date for the translation Smartling translation job"
        )
        self.user = user

    def clean(self) -> dict[str, Any]:
        data = cast(dict[str, Any], super().clean())
        data["user"] = self.user
        return data

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.user = self.user
        if commit:
            obj.save()
            self.save_m2m()
        return obj
