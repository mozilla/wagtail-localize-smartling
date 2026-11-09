from django.utils.translation import gettext_lazy as _
from wagtail.admin.panels import Panel

from .page_locale_table import build_coverage_table


class OrderPagesPanel(Panel):
    """The order's pages, and which locale each one is going to."""

    def __init__(self, **kwargs):
        kwargs.setdefault("heading", _("Pages"))
        super().__init__(**kwargs)

    class BoundPanel(Panel.BoundPanel):
        template_name = "wagtail_localize_smartling/admin/_pages_table.html"

        def is_shown(self):
            # The table is built from rows keyed on the order, so an order that
            # has never been saved has nothing to show.
            return self.instance is not None and self.instance.pk is not None

        def get_context_data(self, parent_context=None):
            context = super().get_context_data(parent_context)
            context["table"] = build_coverage_table(self.instance)
            context["editable"] = self.instance.is_draft
            context["order"] = self.instance
            return context
