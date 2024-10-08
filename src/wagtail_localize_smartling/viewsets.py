import django_filters

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils.functional import cached_property
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _
from wagtail.admin.filters import WagtailFilterSet
from wagtail.admin.ui.tables import Column, DateColumn, TitleColumn, UserColumn
from wagtail.admin.views import generic
from wagtail.admin.viewsets.model import ModelViewSet
from wagtail.permission_policies import ModelPermissionPolicy

from .api.types import JobStatus
from .models import Job
from .templatetags.wagtail_localize_smartling_admin_tags import smartling_job_url


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


class JobInspectView(generic.InspectView):
    def get_fields(self):
        return [
            "translation_source",
            "translations",
            "user",
            "reference_number",
            "status",
            "due_date",
            "first_synced_at",
            "last_synced_at",
            "description",
            "translations_imported_at",
            "content_hash",
        ]

    def get_breadcrumbs_items(self):
        items = []
        if self.index_url_name:
            items.append(
                {
                    "url": reverse(self.index_url_name),
                    "label": _("Smartling jobs"),
                }
            )
        items.append(
            {
                "url": "",
                "label": _("Inspect"),
                "sublabel": self.object.reference_number,
            }
        )
        return self.breadcrumbs_items + items

    def get_field_display_value(self, field_name, field):
        # allow customising field display in the inspect class
        value_func = getattr(self, f"get_{field_name}_display_value", None)
        if value_func is not None:
            return value_func()

        return super().get_field_display_value(field_name, field)

    def get_reference_number_display_value(self):
        if smartling_url := smartling_job_url(self.object):
            return format_html(
                '<a href="{}" title="Reference {}" target="_blank">{}</a>',
                smartling_url,
                self.object.reference_number,
                _("View job in Smartling"),
            )
        return self.object.reference_number

    def get_translation_source_display_value(self):
        return format_html(
            '<a href="{}">{}</a>',
            self.object.translation_source.get_source_instance_edit_url(),
            self.object.translation_source.get_source_instance(),
        )

    def get_translations_display_value(self):
        content_html = format_html_join(
            "\n",
            '<li><a href="{}">{}</a> - {}</li>',
            (
                (
                    translation.get_target_instance_edit_url(),
                    str(translation.get_target_instance()),
                    translation.target_locale.get_display_name(),
                )
                for translation in self.object.translations.all()
            ),
        )

        return format_html("<ul>{}</ul>", content_html)


class JobViewSet(ModelViewSet):
    model = Job
    index_view_class = JobIndexView
    inspect_view_class = JobInspectView
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

    @cached_property
    def url_namespace(self):  # # pyright: ignore[reportIncompatibleVariableOverride]
        return "wagtail_localize_smartling_jobs"


smartling_job_viewset = JobViewSet("smartling-jobs")
