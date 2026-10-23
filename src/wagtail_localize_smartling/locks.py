from copy import copy
from dataclasses import dataclass

from django.db.models import Q
from django.utils.html import format_html
from django.utils.translation import gettext as _
from wagtail.locks import BaseLock

from .constants import UNSYNCED_OR_PENDING_STATUSES
from .models import JobTranslation


_UNSET = object()


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


class PendingTranslationLock(BaseLock):
    """A Page is considered locked if it has content sitting at Smartling awaiting translation."""

    # Give the pending translation message a unique CSS class, so that it can
    # be styled as needed in projects.
    message_class = "smartling-pending-translation-lock"

    def __init__(self, object, locales, is_target=False):
        super().__init__(object)
        self.locales = locales
        self.is_target = is_target

    @property
    def locale_names(self):
        return ", ".join(locale.get_display_name() for locale in self.locales)

    def for_user(self, user):
        return not self.object.can_bypass_pending_translation_lock(user)

    def get_locked_by(self, user):
        return _("Being translated")

    def get_icon(self, user):
        # Registered unconditionally by wagtail-localize.
        return "wagtail-localize-language"

    def get_description(self, user):
        if self.is_target:
            return _("The %(locales)s translation is in progress in Smartling.") % {"locales": self.locale_names}
        return _("This page is being translated in Smartling into %(locales)s.") % {"locales": self.locale_names}

    def get_message(self, user):
        return format_html('<span class="{}">{}</span>', self.message_class, self.get_message_body(user))

    def get_message_body(self, user):
        if self.is_target:
            return format_html(
                _(
                    "<b>The {locales} translation of this page is in progress "
                    "in Smartling.</b> Edits made here would be overwritten "
                    "when it arrives."
                ),
                locales=self.locale_names,
            )

        if self.object.can_bypass_pending_translation_lock(user):
            return format_html(
                _(
                    "<b>This page is being translated in Smartling into {locales}.</b> "
                    "While the Smartling translation is in progress, the page "
                    "should not be edited. Changing a field now would create a "
                    "mismatch between the changed text, and the text translated "
                    "in Smartling."
                ),
                locales=self.locale_names,
            )

        return format_html(
            _(
                "<b>This page is being translated in Smartling into {locales}.</b> "
                "It can't be edited until that translation comes back."
            ),
            locales=self.locale_names,
        )


class CombinedLockMixin(BaseLock):
    """Adds the Smartling restriction to one of Wagtail's own locks."""

    # Always set by combine().
    pending_lock: PendingTranslationLock

    @classmethod
    def combine(cls, native_lock, pending_lock):
        """Return `native_lock` with the Smartling restriction applied on top of it."""
        native_class = type(native_lock)

        # Copy rather than re-class in place, so a project that memoises its own
        # get_lock() does not find its cached lock changed underneath it.
        combined = copy(native_lock)
        combined.__class__ = type(
            f"PendingTranslation{native_class.__name__}",
            (cls, native_class),
            {},
        )
        combined.pending_lock = pending_lock
        return combined

    def for_user(self, user):
        return super().for_user(user) or self.pending_lock.for_user(user)

    def get_message(self, user):
        # The page editor renders the banner straight from this, so the user
        # has to be told which of the two restrictions is holding them.
        if self.pending_lock.for_user(user):
            return self.pending_lock.get_message(user)
        return super().get_message(user)

    def get_context_for_user(self, user, parent_context=None):
        context = super().get_context_for_user(user, parent_context)
        if self.pending_lock.for_user(user):
            context.update(
                {
                    "locked": True,
                    "message": self.pending_lock.get_message(user),
                    "icon": self.pending_lock.get_icon(user),
                    "locked_by": self.pending_lock.get_locked_by(user),
                    "description": self.pending_lock.get_description(user),
                }
            )
        return context


class PendingTranslationLockMixin:
    """
    Mix into a translatable page model to lock it while its content is at Smartling.

    Must precede the Wagtail model class in the MRO, so that get_lock() is reached
    first.
    """

    def can_bypass_pending_translation_lock(self, user):
        """
        Whether a `user` may edit this object despite its content being at Smartling.

        Override this in your project as needed.
        """
        return user.is_superuser

    def get_lock(self):
        # Wagtail enforces one lock, and this project adds PendingTranslationLock.
        # The two are combined here, so they are both used together.
        lock = super().get_lock()
        pending = self._get_pending_translation_lock()
        if lock is None:
            return pending
        if pending is None:
            return lock
        return CombinedLockMixin.combine(lock, pending)

    def _get_pending_translation_lock(self):
        # Memoise for the life of the instance, so different callers can use it.
        if (cached := getattr(self, "_pending_translation_lock", _UNSET)) is not _UNSET:
            return cached

        state = get_pending_translation_state(self)
        if state.is_target:
            # Note: on a target page, this lock blocks unpublishing and
            # submitting for moderation, but  does not block editing, because
            # wagtail-localize's before_edit_page hook overrides the edit view.
            # Warning the user there is already handled by the
            # smartling_edit_translation_message tag.
            lock = PendingTranslationLock(self, [self.locale], is_target=True)
        elif state.locales:
            lock = PendingTranslationLock(self, state.locales)
        else:
            lock = None

        self._pending_translation_lock = lock
        return lock
