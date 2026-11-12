import django_filters

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db.models import Count, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _
from wagtail.admin.filters import WagtailFilterSet
from wagtail.admin.ui.tables import Column, DateColumn, TitleColumn, UserColumn
from wagtail.admin.views import generic
from wagtail.admin.viewsets.model import ModelViewSet
from wagtail.permission_policies import ModelPermissionPolicy

from .models import Order, OrderItem


def get_users_for_filter(user):
    User = get_user_model()
    return User.objects.filter(pk__in=Order.objects.values_list("created_by", flat=True)).order_by(User.USERNAME_FIELD)  # pyright: ignore[reportAttributeAccessIssue]


class OrderReportFilterSet(WagtailFilterSet):
    created_by = django_filters.ModelChoiceFilter(
        label=_("Opened by"),
        field_name="created_by",
        queryset=lambda request: get_users_for_filter(request.user),
    )

    class Meta:
        model = Order
        fields = ["created_by"]


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
    delete_view_class = OrderDeleteView
    filterset_class = OrderReportFilterSet  # pyright: ignore[reportAssignmentType]
    form_fields = ["name", "notes", "due_date"]
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
