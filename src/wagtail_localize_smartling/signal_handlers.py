import logging

from typing import TYPE_CHECKING, Type  # noqa: UP035

from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_save
from wagtail.models import Page
from wagtail.signals import page_published
from wagtail.snippets.models import get_snippet_models

from .models import LandedTranslationTask
from .settings import settings as smartling_settings
from .signals import translation_imported


logger = logging.getLogger(__name__)


if TYPE_CHECKING:
    from django.db.models import Model
    from wagtail_localize.models import Translation

    from wagtail_localize_smartling.models import Job


def _model_is_registered_as_snippet(model: "Model") -> bool:
    """
    Check if a given model has been registered as a Wagtail snippet.
    """
    return model in get_snippet_models()


def create_landed_translation_task(
    sender: Type["Job"],  # noqa: UP006
    instance: "Job",
    translation: "Translation",
    **kwargs,
):
    if not smartling_settings.ADD_APPROVAL_TASK_TO_DASHBOARD:
        logger.info("Creation of a landed-translation task is disabled by settings")
        return

    LandedTranslationTask.objects.create_from_source_and_translation(  # pyright: ignore[reportAttributeAccessIssue]
        source_object=instance.translation_source.get_source_instance(),
        translated_locale=translation.target_locale,
    )


translation_imported.connect(create_landed_translation_task)


def _close_translation_landed_task(instance):
    c_type = ContentType.objects.get_for_model(instance)

    # Try to find the task
    tasks = LandedTranslationTask.objects.filter(
        content_type=c_type,
        object_id=instance.pk,
    )
    for t in tasks:
        t.complete()


def close_translation_landed_task_on_page_published(
    sender: Type["Model"],  # noqa: UP006
    instance: "Model",
    **kwargs,
):
    # Both page_published and post_save send the core args, which
    # is all we need

    if not isinstance(instance, Page):
        logger.debug(
            f"{instance} is not a Page, so not trying to find a landed-translation task"
        )
        return
    return _close_translation_landed_task(instance)


def close_translation_landed_task_on_snippet_published(
    sender: Type["Model"],  # noqa: UP006
    instance: "Model",
    **kwargs,
):
    # Both page_published and post_save send the core args, which
    # is all we need
    if not _model_is_registered_as_snippet(instance):
        logger.debug(
            f"{instance} is not a Snippet, so not trying to find a landed-translation task"
        )
        return
    return _close_translation_landed_task(instance)


page_published.connect(close_translation_landed_task_on_page_published)

post_save.connect(close_translation_landed_task_on_snippet_published)
