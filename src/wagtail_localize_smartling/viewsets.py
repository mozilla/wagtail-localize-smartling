import logging

import django_filters

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db.models import Count, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from wagtail.admin import messages
from wagtail.admin.filters import WagtailFilterSet
from wagtail.admin.panels import FieldPanel, FieldRowPanel
from wagtail.admin.ui.tables import Column, DateColumn, TitleColumn, UserColumn
from wagtail.admin.views import generic
from wagtail.admin.viewsets.model import ModelViewSet
from wagtail.admin.widgets import BooleanRadioSelect
from wagtail.permission_policies import ModelPermissionPolicy

from .models import ORDER_STATES_NEEDING_ATTENTION, Order, OrderItem, OrderState
from .page_locale_table import build_coverage_table
from .panels import OrderPagesPanel
from .send_history import build_send_history


logger = logging.getLogger(__name__)


def get_users_for_filter(user):
    User = get_user_model()
    return User.objects.filter(pk__in=Order.objects.values_list("created_by", flat=True)).order_by(User.USERNAME_FIELD)  # pyright: ignore[reportAttributeAccessIssue]


class OrderReportFilterSet(WagtailFilterSet):
    needs_attention = django_filters.BooleanFilter(
        label=_("Needs attention"),
        method="filter_needs_attention",
        widget=BooleanRadioSelect,
    )
    state = django_filters.ChoiceFilter(
        label=_("Status"),
        choices=OrderState.choices,
        method="filter_state",
        widget=forms.RadioSelect,
        empty_label=_("All"),
    )
    created_by = django_filters.ModelChoiceFilter(
        label=_("Opened by"),
        field_name="created_by",
        queryset=lambda request: get_users_for_filter(request.user),
    )

    class Meta:
        model = Order
        fields = ["needs_attention", "state", "created_by"]

    def filter_needs_attention(self, queryset, name, value):
        if value:
            return queryset.filter(annotated_state__in=ORDER_STATES_NEEDING_ATTENTION)

        return queryset.exclude(annotated_state__in=ORDER_STATES_NEEDING_ATTENTION)

    def filter_state(self, queryset, name, value):
        return queryset.filter(annotated_state=value)


class OrderPermissionPolicy(ModelPermissionPolicy):
    def user_has_permission(self, user, action):
        # An order is opened by adding a page to it from that page's own submit
        # form. The UI here does not offer a way to add an Order, since the user
        # is not given the ability to add pages to it.
        if action == "add":
            return False

        return super().user_has_permission(user, action)

    def user_has_permission_for_instance(self, user, action, instance):
        # A sent order is the record of what went to Smartling, so there is
        # nothing left to edit and nothing to remove.
        if action in {"change", "delete"} and not instance.is_draft:
            return False

        return super().user_has_permission_for_instance(user, action, instance)


class OrderIndexView(generic.IndexView):
    page_title = _("Smartling orders")
    breadcrumbs = []

    def get_breadcrumbs_items(self):  # pyright: ignore[reportIncompatibleMethodOverride]
        return self.breadcrumbs_items + [
            {"url": "", "label": self.page_title},
        ]

    @cached_property
    def columns(self):
        return [
            TitleColumn("name", label=_("Order"), get_url=self.get_row_url),
            Column("page_count", label=_("Pages")),
            Column("state", label=_("Status"), accessor="state_label"),
            UserColumn("created_by", label=_("Opened by")),
            DateColumn("created_at", label=_("Opened"), width="12%"),
            DateColumn("due_date", label=_("Due date"), width="12%"),
        ]

    def get_row_url(self, instance):
        # A draft is still being assembled, so it opens where it can be worked
        # on. Everything else, a reader included, opens the read-only record.
        if instance.is_draft and (edit_url := self.get_edit_url(instance)):
            return edit_url

        return self.get_inspect_url(instance)

    def get_base_queryset(self):
        # The filters run after this, so anything a filter matches on has to be
        # annotated here.
        return Order.annotate_state(
            super()
            .get_base_queryset()
            .annotate(page_count=Coalesce(Subquery(self._get_page_count()), 0))
            # The user column renders an avatar, which the profile holds; without
            # it the listing costs one more query for every row it shows.
            .select_related("created_by__wagtail_userprofile")
        )

    @staticmethod
    def _get_page_count():
        # Counted in a subquery rather than with a join, which would group every
        # column of every row and evaluate the state expression twice per order.
        return (
            OrderItem.objects.filter(order=OuterRef("pk"))
            .order_by()
            .values("order")
            .annotate(page_count=Count("pk"))
            .values("page_count")[:1]
        )


class OrderAlreadySubmitted(Exception):
    """Raised when the edit screen is asked for an order that has been sent."""

    def __init__(self, order: Order):
        super().__init__(f"Order {order.pk} has been submitted")
        self.order = order


class OrderEditView(generic.EditView):
    """The draft screen: an order that is still being assembled."""

    def dispatch(self, request, *args, **kwargs):
        try:
            return super().dispatch(request, *args, **kwargs)
        except OrderAlreadySubmitted as exc:
            messages.info(
                request,
                _("'%(name)s' was sent to Smartling and can no longer be edited.") % {"name": exc.order.name},
            )
            return redirect(reverse(self.inspect_url_name, args=(exc.order.pk,)))

    def get_object(self, queryset=None):
        order = super().get_object(queryset)
        if not order.is_draft:
            raise OrderAlreadySubmitted(order)

        return order

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_count"] = self.object.order_items.count()
        return context

    def post(self, request, *args, **kwargs):
        # A value that is not a pk at all is a malformed request, not a removal,
        # and the ordinary form handling answers it with the screen again.
        if (item_pk := request.POST.get("action-remove-item", "")).isdigit():
            return self._remove_page(request, item_pk)

        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        if "action-submit-order" not in self.request.POST:
            return super().form_valid(form)

        # Saved here rather than through super(), which would add its own
        # "updated" message beside this one and build a redirect that is then
        # thrown away.
        self.object = form.save()
        try:
            self.object.submit(user=self.request.user)
        except ValueError:
            # Every page in the order is already translated into every locale it
            # was ordered for. Smartling was never reached, so repeating the
            # click cannot change the outcome.
            logger.info("Order %s had nothing left to translate", self.object.pk)
            messages.error(
                self.request,
                _(
                    "'%(name)s' has nothing left to translate, so nothing was sent. Tick a locale "
                    "on a page's own Translate form, or remove the pages that are already done."
                )
                % {"name": self.object.name},
            )
            return redirect(self.get_edit_url())
        except Exception:
            # Project.get_current() reaches Smartling on a cold cache, so an
            # outage lands here on an order that is saved but not sent.
            logger.exception("Could not submit order %s", self.object.pk)
            messages.error(
                self.request,
                _("'%(name)s' could not be sent to Smartling. Your changes were saved; try again.")
                % {"name": self.object.name},
            )
            return redirect(self.get_edit_url())

        messages.success(self.request, _("Order '%(name)s' was sent to Smartling.") % {"name": self.object.name})
        self._report_dropped_pages(self.object.dropped_order_items)

        return redirect(self.get_inspect_url())

    def _report_dropped_pages(self, dropped_order_items):
        """Name the pages the send left behind."""
        if not dropped_order_items:
            return

        messages.info(
            self.request,
            ngettext(
                "%(count)d page had nothing left to send and was not included: %(pages)s.",
                "%(count)d pages had nothing left to send and were not included: %(pages)s.",
                len(dropped_order_items),
            )
            % {"count": len(dropped_order_items), "pages": self.object.dropped_page_names},
        )

    def _remove_page(self, request, item_pk):
        self.object = self.get_object()

        removed_count = OrderItem.objects.filter(order=self.object, pk=item_pk).delete()[0]
        if removed_count:
            # Only on a real removal: a second tab clicking the same row would
            # otherwise be told about a page it did not remove.
            messages.info(
                request,
                _(
                    "Removing a page from this order does not delete the translation that was already "
                    "created for it in Wagtail. It only stops that page from being sent to Smartling "
                    "with this order."
                ),
            )
        return redirect(self.get_edit_url())


class OrderInspectView(generic.InspectView):
    """The submitted screen: the record of what an order sent to Smartling."""

    def get_breadcrumbs_items(self):
        items = super().get_breadcrumbs_items()
        index_url = reverse(self.index_url_name) if self.index_url_name else None
        for item in items:
            # super() labels this one after the model, where the screen it
            # links to calls itself something else.
            if index_url is not None and item["url"] == index_url:
                item["label"] = OrderIndexView.page_title
        return items

    def get_edit_url(self):
        # The check behind super() is model-level, and says nothing about this
        # order, which may be one that has been sent.
        if not self.user_has_permission_for_instance("change", self.object):
            return None

        return super().get_edit_url()

    def get_delete_url(self):
        if not self.user_has_permission_for_instance("delete", self.object):
            return None

        return super().get_delete_url()

    def get_field_display_value(self, field_name, field):
        value = super().get_field_display_value(field_name, field)
        # An empty field otherwise reads as the string "None".
        return "\u2014" if value is None else value

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["table"] = build_coverage_table(self.object)
        send_history = build_send_history(self.object, self.request.user)
        context["send_history"] = send_history
        # The send that made the order a record, which is the one whose author
        # the screen names.
        context["first_send"] = send_history[-1] if send_history else None
        return context


class OrderDeleteView(generic.DeleteView):
    def get_object(self, queryset=None):
        order = super().get_object(queryset)
        # DeleteView checks the model-level permission only.
        if not self.user_has_permission_for_instance("delete", order):
            raise PermissionDenied

        return order


class OrderViewSet(ModelViewSet):
    model = Order
    index_view_class = OrderIndexView
    edit_view_class = OrderEditView
    inspect_view_class = OrderInspectView
    delete_view_class = OrderDeleteView
    filterset_class = OrderReportFilterSet  # pyright: ignore[reportAssignmentType]
    panels = [
        FieldRowPanel([FieldPanel("name"), FieldPanel("due_date")]),
        FieldPanel("notes"),
        OrderPagesPanel(),
    ]
    edit_template_name = "wagtail_localize_smartling/admin/order_edit.html"
    inspect_template_name = "wagtail_localize_smartling/admin/order_inspect.html"
    # Notes render on their own below these, because translator notes run long.
    inspect_view_fields = ["name", "due_date"]
    icon = "wagtail-localize-language"
    add_to_admin_menu = True
    menu_label = _("Smartling orders")  # pyright: ignore[reportAssignmentType]
    copy_view_enabled = False
    inspect_view_enabled = True

    @property
    def permission_policy(self):
        return OrderPermissionPolicy(self.model)

    @cached_property
    def url_namespace(self):  # pyright: ignore[reportIncompatibleVariableOverride]
        # Unchanged from when this listed jobs: downstream reverse() calls and
        # bookmarked URLs use this name.
        return "wagtail_localize_smartling_jobs"


smartling_order_viewset = OrderViewSet("smartling-jobs")
