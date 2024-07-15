import django_filters

from django.contrib.auth import get_user_model
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _
from wagtail.admin.filters import WagtailFilterSet
from wagtail.admin.ui.tables import Column, DateColumn, TitleColumn, UserColumn
from wagtail.admin.views import generic
from wagtail.admin.viewsets.model import ModelViewSet
from wagtail.permission_policies import ModelPermissionPolicy

from .api.types import JobStatus
from .models import Job


def get_users_for_filter(user):
    User = get_user_model()
    return User.objects.filter(
        pk__in=Job.objects.values_list("user", flat=True)
    ).order_by(User.USERNAME_FIELD)  # pyright: ignore[reportAttributeAccessIssue]


class JobReportFilterSet(WagtailFilterSet):
    status = django_filters.ChoiceFilter(choices=JobStatus.choices)
    user = django_filters.ModelChoiceFilter(
        label=_("User"),
        field_name="user",
        queryset=lambda request: get_users_for_filter(request.user),
    )

    class Meta:
        model = Job
        fields = ["status"]


class JobPermissionPolicy(ModelPermissionPolicy):
    def user_has_permission(self, user, action):
        if action in ["add", "change"]:
            return False

        return super().user_has_permission(user, action)


class SourceInstanceColumn(TitleColumn):
    def get_value(self, instance):
        return instance.translation_source.get_source_instance()

    def get_link_attrs(self, instance, parent_context):
        return {"title": _(f"Job {instance.name}")}


class TargetLocalesColumn(Column):
    """Outputs a list of job target locales"""

    cell_template_name = (
        "wagtail_localize_smartling/admin/tables/target_locales_cell.html"
    )

    def get_value(self, instance):
        return getattr(instance, self.accessor).all()


class JobIndexView(generic.IndexView):
    page_title = _("Smartling jobs")
    breadcrumbs = []

    def get_breadcrumbs_items(self):
        return self.breadcrumbs_items + [
            {"url": "", "label": self.page_title},
        ]

    @cached_property
    def columns(self):
        columns = [
            SourceInstanceColumn(
                "source_instance",
                label=_("Source"),
                get_url=self.get_inspect_url,
            ),
            TargetLocalesColumn(
                "translations",
                label=_("Target locales"),
            ),
            DateColumn(
                "due_date",
                label=_("Due date"),
                width="12%",
            ),
            Column("status", label=_("Status"), accessor="get_status_display"),
            DateColumn(
                "first_synced_at",
                label=_("First synced at"),
                width="12%",
            ),
            DateColumn(
                "last_synced_at",
                label=_("Last synced at"),
                width="12%",
            ),
            UserColumn("user"),
        ]
        return columns

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.select_related("translation_source").prefetch_related(
            "translations__target_locale"
        )


class JobViewSet(ModelViewSet):
    model = Job
    index_view_class = JobIndexView
    filterset_class = JobReportFilterSet  # pyright: ignore[reportAssignmentType]
    form_fields = ["status"]
    icon = "wagtail-localize-language"
    add_to_admin_menu = True
    menu_label = _("Smartling jobs")  # pyright: ignore[reportAssignmentType]
    copy_view_enabled = False
    inspect_view_enabled = True

    @property
    def permission_policy(self):
        return JobPermissionPolicy(self.model)


smartling_job_viewset = JobViewSet("smartling-jobs")
