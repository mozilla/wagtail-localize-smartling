import logging

from typing import TYPE_CHECKING, Type  # noqa: UP035

from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.core.mail import send_mail
from django.db.models.signals import post_save
from django.template.loader import render_to_string
from wagtail.models import Page
from wagtail.signals import page_published
from wagtail.snippets.models import get_snippet_models

from wagtail_localize_smartling.models import LandedTranslationTask
from wagtail_localize_smartling.settings import settings as smartling_settings
from wagtail_localize_smartling.signals import (
    individual_translation_imported,
    translation_import_successful,
)


if TYPE_CHECKING:
    from django.db.models import Model
    from wagtail_localize.models import Translation

    from wagtail_localize_smartling.models import Job


logger = logging.getLogger(__name__)


def _model_is_registered_as_snippet(model: Type["Model"]) -> bool:  # noqa: UP006
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
        logger.debug("Creation of a landed-translation task is disabled by settings")
        return

    LandedTranslationTask.objects.create_from_source_and_translation(  # pyright: ignore[reportAttributeAccessIssue]
        source_object=instance.translation_source.get_source_instance(),
        translated_locale=translation.target_locale,
    )


individual_translation_imported.connect(create_landed_translation_task, weak=False)


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
        logger.debug(f"{instance} is not a Page, so not trying to find a landed-translation task")
        return
    return _close_translation_landed_task(instance)


page_published.connect(close_translation_landed_task_on_page_published, weak=False)


def close_translation_landed_task_on_snippet_published(
    sender: Type["Model"],  # noqa: UP006
    instance: "Model",
    **kwargs,
):
    # Both page_published and post_save send the core args, which
    # is all we need

    # Skip during fixture loading (raw=True) — related objects may not be
    # available yet, and landed-translation tasks should not be looked up or
    # completed from fixture-loaded saves.
    if kwargs.get("raw"):
        logger.debug(f"{sender} was saved with raw=True, so not trying to find a landed-translation task")
        return

    if not _model_is_registered_as_snippet(sender):
        logger.debug(f"{instance} is not a Snippet, so not trying to find a landed-translation task")
        return
    return _close_translation_landed_task(instance)


post_save.connect(close_translation_landed_task_on_snippet_published, weak=False)


def _build_translated_target_links(translations_imported: list["Translation"]):
    """
    Return a list of dicts with `locale_name` and `edit_url` for each
    imported Translation, ready to render into the notification email.
    `edit_url` is absolutised via WAGTAILADMIN_BASE_URL when set; if the
    target instance can't be looked up (e.g. it was deleted between
    import and notification), the entry is included with an empty URL
    so the locale still appears in the email body.
    """
    base_url = (getattr(settings, "WAGTAILADMIN_BASE_URL", "") or "").rstrip("/")
    links = []
    for translation in translations_imported:
        try:
            path = translation.get_target_instance_edit_url()
        except Exception:
            logger.warning(
                "Could not resolve edit URL for translation %s; omitting link from notification email",
                getattr(translation, "pk", "<unknown>"),
            )
            path = ""
        edit_url = f"{base_url}{path}" if (base_url and path) else path
        links.append(
            {
                "locale_name": translation.target_locale.language_name,
                "edit_url": edit_url,
            }
        )
    return links


def send_email_notification_upon_overall_translation_import(
    sender: Type["Job"],  # noqa: UP006
    instance: "Job",
    translations_imported: list["Translation"],
    **kwargs,
):
    """
    Signal handler for receiving news that a translation has landed from
    Smartling. Sends a notification email to all Translation Approvers
    AND to the user who originally submitted the Job, so the submitter
    learns when their pages are ready without needing firehose-level
    membership in the Translation Approver group.
    """
    if not smartling_settings.SEND_EMAIL_ON_TRANSLATION_IMPORT:
        logger.info("Email notifications following translation import are disabled by settings")
        return

    ta_group = Group.objects.filter(name=smartling_settings.TRANSLATION_APPROVER_GROUP_NAME)
    if ta_group.exists():
        # Group names are unique, so this is safe as a get()
        ta_group = ta_group.get()
        approver_email_addresses = list(
            ta_group.user_set.filter(is_active=True).values_list(  # pyright: ignore[reportAttributeAccessIssue]
                "email",
                flat=True,
            )
        )
    else:
        logger.warning(
            f"Could not find the {smartling_settings.TRANSLATION_APPROVER_GROUP_NAME} "
            "auth.Group; will attempt to notify the Job submitter if one is available"
        )
        approver_email_addresses = []

    submitter_email = getattr(instance.user, "email", "") if instance.user else ""

    # Build a deduped, order-preserving recipient list: group members
    # first, submitter appended only if not already present.
    recipient_list = []
    seen = set()
    for email in [*approver_email_addresses, submitter_email]:
        if email and email not in seen:
            recipient_list.append(email)
            seen.add(email)

    if not recipient_list:
        logger.warning(
            "Unable to send translation-imported email notifications: "
            f"no members of the {smartling_settings.TRANSLATION_APPROVER_GROUP_NAME} "
            "group in the system and no Job submitter email available"
        )
        return

    email_subject = render_to_string(
        template_name="wagtail_localize_smartling/admin/email/notifications/translations_imported__subject.txt"
    ).replace("\n", "")

    email_body = render_to_string(
        template_name="wagtail_localize_smartling/admin/email/notifications/translations_imported__body.txt",
        context={
            "job_name": instance.name,
            "translation_source_name": str(instance.translation_source.get_source_instance()),
            "translation_targets": _build_translated_target_links(translations_imported),
        },
    )

    send_mail(
        subject=email_subject,
        message=email_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=recipient_list,
    )
    logger.info(f"Translation-imported notification sent to {len(recipient_list)} users")


translation_import_successful.connect(send_email_notification_upon_overall_translation_import, weak=False)
