import logging

from typing import TYPE_CHECKING, Type  # noqa: UP035

import requests

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
    job_synced_to_smartling,
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


def send_email_notification_upon_overall_translation_import(
    sender: Type["Job"],  # noqa: UP006
    instance: "Job",
    translations_imported: list["Translation"],
    **kwargs,
):
    """
    Signal handler for receiving news that a translation has landed from
    Smartling.

    For now, sends a notification email to all Translation Approvers. TODO: make this a custom
    notification option for all users in the Translation Approver group
    """
    if not smartling_settings.SEND_EMAIL_ON_TRANSLATION_IMPORT:
        logger.info("Email notifications following translation import are disabled by settings")
        return

    ta_group = Group.objects.filter(name=smartling_settings.TRANSLATION_APPROVER_GROUP_NAME)
    if not ta_group.exists():
        logger.warning(
            f"Could not find the {smartling_settings.TRANSLATION_APPROVER_GROUP_NAME} "
            "auth.Group to send email notifications to"
        )
        return

    # Group names are unique, so this is safe as a get()
    ta_group = ta_group.get()

    approver_email_addresses = ta_group.user_set.filter(is_active=True).values_list(  # pyright: ignore[reportAttributeAccessIssue]
        "email",
        flat=True,
    )
    approver_email_addresses = [
        email for email in approver_email_addresses if email
    ]  # Safety check to ensure no empty email addresses are included

    if not approver_email_addresses:
        logger.warning(
            "Unable to send translation-imported email notifications: "
            f"no members of the {smartling_settings.TRANSLATION_APPROVER_GROUP_NAME} "
            "group in the system"
        )
        return

    email_subject = render_to_string(
        template_name="wagtail_localize_smartling/admin/email/notifications/translations_imported__subject.txt"
    ).replace("\n", "")

    job_name = instance.name
    translation_source_name = str(instance.translation_source.get_source_instance())

    email_body = render_to_string(
        template_name="wagtail_localize_smartling/admin/email/notifications/translations_imported__body.txt",
        context={
            "job_name": job_name,
            "translation_source_name": translation_source_name,
            "translation_target_locales": [x.target_locale for x in translations_imported],
        },
    )

    send_mail(
        subject=email_subject,
        message=email_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=approver_email_addresses,
    )
    logger.info(f"Translation-imported notification sent to {len(approver_email_addresses)} users")


translation_import_successful.connect(send_email_notification_upon_overall_translation_import, weak=False)


_DEFAULT_SLACK_NEW_JOB_NOTIFICATION_MESSAGE_TEMPLATE = (
    "A new Smartling translation job *{job_name}* has been synced and is awaiting authorization.\n"
    "Project: {project_name}\n"
    "Description: {job_description}\n"
    "Word count: {word_count}\n"
    "Locales ({locale_count}): {locale_list}"
)


def send_slack_notification_on_job_sync(
    sender: Type["Job"],  # noqa: UP006
    instance: "Job",
    **kwargs,
):
    if not smartling_settings.SLACK_BOT_TOKEN or not smartling_settings.SLACK_NEW_JOB_NOTIFICATION_CHANNEL_ID:
        logger.debug("Slack new-job notifications are not configured, skipping")
        return

    template = (
        smartling_settings.SLACK_NEW_JOB_NOTIFICATION_MESSAGE_TEMPLATE
        or _DEFAULT_SLACK_NEW_JOB_NOTIFICATION_MESSAGE_TEMPLATE
    )

    po = instance.translation_source.export_po()
    word_count = sum(len(entry.msgid.split()) for entry in po)
    locale_codes = list(instance.translations.values_list("target_locale__language_code", flat=True))
    locale_count = len(locale_codes)
    locale_list = ", ".join(locale_codes)

    try:
        message = template.format(
            job_name=instance.name,
            job_description=instance.description,
            project_name=instance.project.name,
            word_count=word_count,
            locale_count=locale_count,
            locale_list=locale_list,
        )
        response = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={
                "Authorization": f"Bearer {smartling_settings.SLACK_BOT_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "channel": smartling_settings.SLACK_NEW_JOB_NOTIFICATION_CHANNEL_ID,
                "text": message,
            },
            timeout=5,
        )
        response_data = response.json()
        if not response_data.get("ok"):
            logger.error(
                "Slack API returned an error when sending new-job notification: %s",
                response_data.get("error", "unknown error"),
            )
        else:
            logger.info("Slack new-job notification sent for job %s", instance.name)
    except Exception:
        logger.exception("Failed to send Slack new-job notification for job %s", instance.name)


job_synced_to_smartling.connect(send_slack_notification_on_job_sync, weak=False)
