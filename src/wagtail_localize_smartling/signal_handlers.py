import logging

from typing import TYPE_CHECKING, Type  # noqa: UP035

from .models import LandedTranslationTask
from .settings import settings as smartling_settings
from .signals import translation_imported


logger = logging.getLogger(__name__)


if TYPE_CHECKING:
    from wagtail_localize.models import Translation

    from wagtail_localize_smartling.models import Job


def create_landed_translation_task(
    sender: Type["Job"],  # noqa: UP006
    instance: "Job",
    translation: "Translation",
    **kwargs,
):
    if not smartling_settings.ADD_APPROVAL_TASK_TO_DASHBOARD:
        logger.info("Creation of a landed-translation task is disabled by settings")
        return

    LandedTranslationTask.objects.create_from_source_and_translation(  # type: ignore
        source_object=instance.translation_source.get_source_instance(),
        translated_locale=translation.target_locale,
    )


translation_imported.connect(create_landed_translation_task)
