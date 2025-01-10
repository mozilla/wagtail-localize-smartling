from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from laces.typing import RenderContext

from wagtail.admin.ui.components import Component

from .models import LandedTranslationTask


class LandedTranslationsPanel(Component):
    order = 150
    template_name = "wagtail_localize_smartling/admin/components/_landed_translation_task_panel.html"  # noqa: E501

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
