from typing import Any, cast

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from wagtail.admin.forms import WagtailAdminModelForm

from .settings import settings as smartling_settings


class JobForm(WagtailAdminModelForm):
    """
    Custom ModelForm for the Job model. We need to add the "user" key to the
    validated data _and_ set the attribute on the instance because of the way
    wagtail-localize's translation components work.
    """

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["due_date"].help_text = _("Optional due date for the translation Smartling translation job")
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


class ResubmitOrderForm(forms.Form):
    """Names the Smartling job that resending an order will create."""

    def __init__(self, *args, order, **kwargs):
        # Imported here because models.py imports this module.
        from .models import Job

        kwargs.setdefault("initial", {"job_name": order.suggested_job_name()})
        super().__init__(*args, **kwargs)
        self.order = order
        self.fields["job_name"] = forms.CharField(
            label=_("Job name"),
            help_text=_("Smartling refuses a name one of its jobs already has."),
            # The name is stored as the job's own, so it cannot outgrow it.
            max_length=Job._meta.get_field("name").max_length,  # pyright: ignore[reportAttributeAccessIssue]
        )

    def clean_job_name(self) -> str:
        # Imported here because models.py imports this module.
        from .models import Job, Order

        job_name = self.cleaned_data["job_name"]
        # Checked here because Smartling's own rejection arrives in a
        # background task, which a user won't see.
        if Job.objects.filter(name__iexact=job_name).exists():
            raise ValidationError(
                _("A Smartling job is already called “%(name)s”. Pick another name.") % {"name": job_name}
            )

        prefix = smartling_settings.JOB_NAME_PREFIX
        order_name = job_name
        if prefix:
            # The prefix is how this project's jobs are picked out at the
            # Smartling end.
            if not job_name.startswith(f"{prefix} "):
                raise ValidationError(
                    _("Keep the “%(prefix)s ” that every job from here carries.") % {"prefix": prefix}
                )
            order_name = job_name[len(prefix) + 1 :]

        # Any other order's name is refused.
        if Order.objects.exclude(pk=self.order.pk).filter(name__iexact=order_name).exists():
            raise ValidationError(_("An order is already called “%(name)s”. Pick another name.") % {"name": order_name})

        return job_name
