from typing import TYPE_CHECKING, Any, cast

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.utils.timesince import timesince
from django.utils.translation import gettext, ngettext
from django.utils.translation import gettext_lazy as _
from wagtail.admin.forms import WagtailAdminModelForm

from .settings import settings as smartling_settings


if TYPE_CHECKING:
    from .models import Order


class JobForm(WagtailAdminModelForm):
    """
    Custom ModelForm for the Job model. We need to add the "user" key to the
    validated data _and_ set the attribute on the instance because of the way
    wagtail-localize's translation components work.

    It also carries the choice between sending the page to Smartling on its
    own and collecting it into an order, which reaches the model as the
    `order` field.
    """

    SEND_NOW = "now"
    NEW_ORDER = "new"
    EXISTING_ORDER = "existing"

    how_to_send_choice = forms.ChoiceField(
        label=_("How to send this"),
        required=False,
        initial=SEND_NOW,
        widget=forms.RadioSelect,
    )
    new_order_notes = forms.CharField(label=_("Notes for translators"), required=False, widget=forms.Textarea)

    # The how_to_send_choice field determines which other fields should be empty
    FIELDS_THAT_SHOULD_BE_EMPTY = {
        # value of how_to_send_choice: (fields that should be empty),
        SEND_NOW: ("order", "new_order_name", "new_order_notes"),
        NEW_ORDER: ("order",),
        EXISTING_ORDER: ("new_order_name", "new_order_notes"),
    }

    def __init__(self, *args, user, **kwargs):
        # Imported here because models.py imports this module.
        from .models import Order

        super().__init__(*args, **kwargs)
        self.fields["new_order_name"] = forms.CharField(
            label=_("Order name"),
            required=False,
            # The name becomes the order's own, so it cannot outgrow it.
            max_length=Order._meta.get_field("name").max_length,  # pyright: ignore[reportAttributeAccessIssue]
        )
        self.fields["due_date"].help_text = _(
            "Applies to this job, or to the new order. Adding to an open order uses "
            "that order's own due date, which you can change on the order."
        )
        self.user = user

        open_orders = Order.objects.filter(submitted_at__isnull=True).annotate(page_count=Count("order_items"))
        self.fields["order"].label = _("Open order")
        self.fields["order"].queryset = open_orders  # pyright: ignore[reportAttributeAccessIssue]
        self.fields["order"].label_from_instance = self._describe_order  # pyright: ignore[reportAttributeAccessIssue]

        how_to_send_choices = [
            (self.SEND_NOW, _("Send now as its own job")),
            (self.NEW_ORDER, _("Start a new order…")),
        ]
        if open_orders.exists():
            how_to_send_choices.append((self.EXISTING_ORDER, _("Add to an open order…")))
        else:
            # An empty dropdown is a choice nobody can make, so neither it
            # nor the radio that governs it is shown.
            self.fields["order"].widget = forms.HiddenInput()
        self.fields["how_to_send_choice"].choices = how_to_send_choices  # pyright: ignore[reportAttributeAccessIssue]

        # The "how_to_send_choice" field governs the fields below it, so it
        # should render before them.
        self.order_fields(["enabled", "how_to_send_choice", "new_order_name", "new_order_notes", "order", "due_date"])

    @staticmethod
    def _describe_order(order: "Order") -> str:
        pages = ngettext("%(count)s page", "%(count)s pages", order.page_count) % {"count": order.page_count}
        # created_by is cleared when the account is deleted, and an order can
        # outlive whoever opened it.
        opened_by = order.created_by.get_username() if order.created_by else gettext("a deleted user")
        return gettext("%(name)s — %(pages)s, opened by %(user)s, %(age)s ago") % {
            "name": order.name,
            "pages": pages,
            "user": opened_by,
            "age": timesince(order.created_at),
        }

    def clean(self) -> dict[str, Any]:
        # Imported here because models.py imports this module.
        from .models import Order

        data = cast(dict[str, Any], super().clean())
        data["user"] = self.user

        how_to_send_choice = data.get("how_to_send_choice") or self.SEND_NOW

        # Every field renders whatever the radio says, so a filled-in field the
        # radio leaves unread is a disagreement, not a leftover. The radio
        # decides, and silently discarding the rest would send a page to
        # Smartling on its own, which cannot be undone.
        fields_that_should_be_empty_but_are_not = [
            self.fields[name].label for name in self.FIELDS_THAT_SHOULD_BE_EMPTY[how_to_send_choice] if data.get(name)
        ]
        if fields_that_should_be_empty_but_are_not:
            self.add_error(
                "how_to_send_choice",
                ngettext(
                    "The value of %(fields)s is not valid for this choice.",
                    "The values of %(fields)s are not valid for this choice.",
                    len(fields_that_should_be_empty_but_are_not),
                )
                % {"fields": ", ".join(f"“{label}”" for label in fields_that_should_be_empty_but_are_not)},
            )
            return data

        if how_to_send_choice == self.EXISTING_ORDER:
            if not data.get("order"):
                self.add_error("order", _("Choose an order, or start a new one."))
        elif how_to_send_choice == self.NEW_ORDER:
            name = (data.get("new_order_name") or "").strip()
            if not name:
                self.add_error("new_order_name", _("Give the order a name."))
            else:
                # Left unsaved: an order is written to the database only once a
                # page has joined it, so a submission that ends up adding
                # nothing leaves no empty order behind.
                data["order"] = Order(
                    name=name,
                    notes=data.get("new_order_notes") or "",
                    due_date=data.get("due_date"),
                    created_by=self.user,
                )

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
