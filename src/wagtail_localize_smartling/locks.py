from dataclasses import dataclass

from django.db.models import Q

from .constants import UNSYNCED_OR_PENDING_STATUSES
from .models import JobTranslation


@dataclass(frozen=True)
class PendingTranslationState:
    is_target: bool
    locales: list


def get_pending_translation_state(instance):
    """Return the current state of an instance."""
    rows = (
        JobTranslation.objects.filter(
            Q(translation__source__locale_id=instance.locale_id) | Q(translation__target_locale_id=instance.locale_id),
            job__status__in=UNSYNCED_OR_PENDING_STATUSES,
            imported_at__isnull=True,
            translation__enabled=True,
            translation__source__object_id=instance.translation_key,
        )
        .select_related("translation__source", "translation__target_locale")
        .order_by("translation__target_locale__language_code")
    )

    is_target = False
    locales = {}
    for row in rows:
        translation = row.translation
        if translation.target_locale_id == instance.locale_id:
            is_target = True
        if translation.source.locale_id == instance.locale_id:
            locales.setdefault(translation.target_locale_id, translation.target_locale)

    return PendingTranslationState(is_target=is_target, locales=list(locales.values()))


def get_in_flight_locales_for_instance(instance):
    """Return the Locales that have unfinished work in Smartling for this instance."""
    return get_pending_translation_state(instance).locales


def is_in_flight_target(instance):
    """
    Return whether an instance is the translated object for a locale that still has
    work at Smartling.
    Editing it now means losing that work on import.
    """
    return get_pending_translation_state(instance).is_target
