from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from laces.typing import RenderContext

from django.db.models import Count, Subquery
from wagtail.admin.ui.components import Component

from .models import LandedTranslationTask, Order


class LandedTranslationsPanel(Component):
    order = 150
    template_name = "wagtail_localize_smartling/admin/components/_landed_translation_task_panel.html"

    def __init__(self, *args, **kwargs):
        self.max_to_show = kwargs.pop("max_to_show", None)

        super().__init__(*args, **kwargs)

    def get_all_landed_translation_tasks(self):
        return LandedTranslationTask.objects.incomplete()  # pyright: ignore[reportAttributeAccessIssue]

    def get_context_data(self, parent_context: "RenderContext | None" = None):
        context = {}
        all_tasks = self.get_all_landed_translation_tasks()
        context["tasks"] = all_tasks[: self.max_to_show]
        context["total_tasks"] = all_tasks.count()
        return context


class OpenOrdersPanel(Component):
    """Orders nobody has sent yet, and sends that did not finish."""

    order = 160
    template_name = "wagtail_localize_smartling/admin/components/_open_orders_panel.html"

    def __init__(self, *args, user=None, max_to_show=None, **kwargs):
        self.user = user
        self.max_to_show = max_to_show

        super().__init__(*args, **kwargs)

    def get_context_data(self, parent_context: "RenderContext | None" = None):
        # Oldest first throughout: the one that has waited longest is the one
        # most likely to have been forgotten.
        open_orders = (
            Order.objects.filter(submitted_at__isnull=True)
            .annotate(count_pages=Count("order_items"))
            .order_by("created_at")
        )
        latest_job = Order.latest_job_subquery()
        failing_orders = (
            Order.objects.filter(submitted_at__isnull=False)
            .annotate(
                latest_sync_error_at=Subquery(latest_job.values("last_sync_error_at")[:1]),
                latest_sync_blocked=Subquery(latest_job.values("sync_blocked")[:1]),
            )
            .filter(latest_sync_error_at__isnull=False)
            .order_by("latest_sync_error_at")
        )

        groups = {
            "open_orders": self._group(open_orders),
            "blocked_orders": self._group(failing_orders.filter(latest_sync_blocked=True)),
            "stalled_orders": self._group(failing_orders.filter(latest_sync_blocked=False)),
        }
        return {
            **groups,
            # Only show the panel if there are any (non-empty) groups.
            "has_orders": any(group["orders"] for group in groups.values()),
            # An order nobody may edit still opens, read-only, on its own screen.
            "open_order_url_name": (
                "wagtail_localize_smartling_jobs:edit"
                if self.user is not None and self.user.has_perm("wagtail_localize_smartling.change_order")
                else "wagtail_localize_smartling_jobs:inspect"
            ),
        }

    def _group(self, orders):
        shown_orders = list(orders[: self.max_to_show])
        count_total = (
            orders.count()
            if self.max_to_show is not None and len(shown_orders) == self.max_to_show
            else len(shown_orders)
        )
        return {
            "orders": shown_orders,
            "count_total": count_total,
            "count_remaining": count_total - len(shown_orders),
        }
