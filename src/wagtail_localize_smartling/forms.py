from functools import cached_property
from typing import TYPE_CHECKING, Any, cast

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.utils.timesince import timesince
from django.utils.translation import gettext, ngettext
from django.utils.translation import gettext_lazy as _
from wagtail.admin.forms import WagtailAdminModelForm
from wagtail.models import Page

from .settings import settings as smartling_settings
from .utils import job_name_begins_with_prefix, name_without_job_prefix


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

    def __init__(self, *args, user, source_object_instance=None, **kwargs):
        # Imported here because models.py imports this module.
        from .models import Order

        super().__init__(*args, **kwargs)
        self.descendant_count = (
            source_object_instance.get_descendants().count() if isinstance(source_object_instance, Page) else 0
        )
        self.fields["new_order_name"] = forms.CharField(
            label=_("Order name"),
            required=False,
            # The name becomes the order's own, so it cannot outgrow it.
            max_length=Order._meta.get_field("name").max_length,  # pyright: ignore[reportAttributeAccessIssue]
        )
        if self.descendant_count:
            # A page with children may go in as a subtree, which needs an
            # order, so the obvious name is offered — unless it is one the
            # validation below would refuse, which a second send of the same
            # section would hit.
            suggested_order_name = str(source_object_instance)[: self.fields["new_order_name"].max_length]
            if not self._order_name_error(suggested_order_name):
                self.fields["new_order_name"].initial = suggested_order_name
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
        # Someone who picked an order that has since been sent still needs the
        # option on screen, or validation would refuse the radio instead and
        # say nothing about the order.
        if open_orders.exists() or self.chosen_order_already_sent:
            how_to_send_choices.append((self.EXISTING_ORDER, _("Add to an open order…")))
        else:
            # An empty dropdown is a choice nobody can make, so neither it
            # nor the radio that governs it is shown.
            self.fields["order"].widget = forms.HiddenInput()
        self.fields["how_to_send_choice"].choices = how_to_send_choices  # pyright: ignore[reportAttributeAccessIssue]
        if self.descendant_count:
            # Each open order says how many pages it holds; this is the size
            # of what would join it.
            count_pages_in_subtree = self.descendant_count + 1
            self.fields["how_to_send_choice"].help_text = ngettext(
                "This page and its subtree are %(count)s page.",
                "This page and its subtree are %(count)s pages.",
                count_pages_in_subtree,
            ) % {"count": count_pages_in_subtree}

        # The "how_to_send_choice" field governs the fields below it, so it
        # should render before them.
        self.order_fields(["enabled", "how_to_send_choice", "new_order_name", "new_order_notes", "order", "due_date"])

    @cached_property
    def adds_a_subtree(self) -> bool:
        """Whether this submission carries the object's descendants too."""
        # include_subtree belongs to wagtail-localize's own form, which posts
        # alongside this one and carries no prefix.
        is_subtree_included = forms.CheckboxInput().value_from_datadict(self.data, self.files, "include_subtree")
        return bool(is_subtree_included) and self.descendant_count > 0

    @staticmethod
    def _describe_order(order: "Order") -> str:
        page_count = order.page_count  # pyright: ignore[reportAttributeAccessIssue]
        pages = ngettext("%(count)s page", "%(count)s pages", page_count) % {"count": page_count}
        # created_by is cleared when the account is deleted, and an order can
        # outlive whoever opened it.
        opened_by = order.created_by.get_username() if order.created_by else gettext("a deleted user")
        return gettext("%(name)s — %(pages)s, opened by %(user)s, %(age)s ago") % {
            "name": order.name,
            "pages": pages,
            "user": opened_by,
            "age": timesince(order.created_at),
        }

    @staticmethod
    def _order_name_error(order_name: str) -> str | None:
        """Why this order cannot be given this name, or None if it can."""
        # Imported here because models.py imports this module.
        from .models import Order

        try:
            Order(name=order_name).clean()
        except ValidationError as error:
            # An open order is the one refusal this screen can do something
            # about, so it gives the user its own error message for it.
            if Order.objects.filter(submitted_at__isnull=True, name__iexact=order_name).exists():
                return gettext("An order called '%(name)s' is already open. Add to it, or pick another name.") % {
                    "name": order_name
                }

            return error.message_dict["name"][0]

        return None

    @cached_property
    def chosen_order_already_sent(self) -> "Order | None":
        """The order that was picked, if it has since been sent to Smartling."""
        # Imported here because models.py imports this module.
        from .models import Order

        # Only open orders are offered, so one sent since this form was
        # rendered survives nowhere but the raw POST data.
        try:
            return Order.objects.filter(pk=self.data.get(self.add_prefix("order")), submitted_at__isnull=False).first()
        except (TypeError, ValueError):
            # Not a primary key at all, which the field itself reports.
            return None

    def clean(self) -> dict[str, Any]:
        # Imported here because models.py imports this module.
        from .models import Order

        data = cast(dict[str, Any], super().clean())
        data["user"] = self.user

        how_to_send_choice = data.get("how_to_send_choice") or self.SEND_NOW

        if how_to_send_choice == self.SEND_NOW and self.adds_a_subtree:
            # Sending now can not go through the entire subtree and add all
            # descendant pages to it. Instead, that is done as a background
            # task.
            self.add_error(
                "how_to_send_choice",
                _(
                    "A subtree can not be sent right now. To send the subtree "
                    "with this page, either add the page and its subtree to an "
                    "open order, or start a new order for the page and its "
                    "subtree."
                ),
            )
            return data

        # Every field renders whatever the radio says, so a filled-in field the
        # radio leaves unread is a disagreement, not a leftover. The radio
        # decides, and silently discarding the rest would send a page to
        # Smartling on its own, which cannot be undone.
        fields_that_should_be_empty_but_are_not = [
            self.fields[name].label
            for name in self.FIELDS_THAT_SHOULD_BE_EMPTY[how_to_send_choice]
            if data.get(name) and name in self.changed_data
        ]
        if fields_that_should_be_empty_but_are_not:
            self.add_error(
                "how_to_send_choice",
                ngettext(
                    "The value of %(fields)s is not valid for this choice.",
                    "The values of %(fields)s are not valid for this choice.",
                    len(fields_that_should_be_empty_but_are_not),
                )
                % {"fields": ", ".join(f"'{label}'" for label in fields_that_should_be_empty_but_are_not)},
            )
            return data

        if how_to_send_choice == self.EXISTING_ORDER and not data.get("order"):
            if order_already_sent := self.chosen_order_already_sent:
                # The user chose an order that the field has now refused,
                # because it was sent between the time the user loaded the form
                # and submitted the form.
                self.errors.pop("order", None)
                if self.adds_a_subtree:
                    # Sending a subtree as its own job is refused, so it is
                    # not advised here.
                    advice = _(
                        "%(name)s was sent to Smartling while you were on this page. "
                        "Start a new order for this page and its subtree."
                    )
                else:
                    advice = _(
                        "%(name)s was sent to Smartling while you were on this page."
                        "Send this page as its own job, or start a new order."
                    )
                self.add_error("order", advice % {"name": order_already_sent.name})
            else:
                self.add_error("order", _("Choose an order, or start a new one."))
        elif how_to_send_choice == self.NEW_ORDER and not self.has_error("new_order_name"):
            # If there is not already a new_order_name error, we run our own
            # validation on the field.
            name = (data.get("new_order_name") or "").strip()
            if not name:
                self.add_error("new_order_name", _("Give the order a name."))
            elif name_error := self._order_name_error(name):
                self.add_error("new_order_name", name_error)
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
        if Job.name_is_taken(job_name):
            raise ValidationError(
                _("A Smartling job is already called '%(name)s'. Pick another name.") % {"name": job_name}
            )

        if not job_name_begins_with_prefix(job_name):
            raise ValidationError(
                _("Keep the '%(prefix)s ' that every job from here carries.")
                % {"prefix": smartling_settings.JOB_NAME_PREFIX}
            )
        order_name = name_without_job_prefix(job_name)

        # Any other order's name is refused.
        if Order.objects.exclude(pk=self.order.pk).filter(name__iexact=order_name).exists():
            raise ValidationError(_("An order is already called '%(name)s'. Pick another name.") % {"name": order_name})

        return job_name
