# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from unittest.mock import Mock

import pytest

from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.test import override_settings
from wagtail.models import Page
from wagtail_localize.models import Translation

from wagtail_localize_smartling.models import Job, LandedTranslationTask
from wagtail_localize_smartling.signal_handlers import (
    _close_translation_landed_task,
    _model_is_registered_as_snippet,
    close_translation_landed_task_on_page_published,
    close_translation_landed_task_on_snippet_published,
    create_landed_translation_task,
    send_email_notification_upon_overall_translation_import,
)
from wagtail_localize_smartling.signals import (
    individual_translation_imported,
    translation_import_successful,
)

from tests.factories import TranslationApproverGroupFactory, WagtailUserFactory


pytestmark = [pytest.mark.django_db]


def test_individual_translation_imported_is_connected_to_the_expected_handlers():
    assert len(individual_translation_imported.receivers) == 1
    assert (
        individual_translation_imported.receivers[0][1]
        == create_landed_translation_task
    )


def test_translation_import_successful_is_connected_to_the_expected_handlers():
    assert len(translation_import_successful.receivers) == 1
    assert (
        translation_import_successful.receivers[0][1]
        == send_email_notification_upon_overall_translation_import
    )


@override_settings(
    DEFAULT_FROM_EMAIL="from@example.com",
    WAGTAILADMIN_BASE_URL="https://cms.example.com",
)
def test_notify_of_imported_translations__happy_path(mocker):
    mock_logger_info = mocker.patch(
        "wagtail_localize_smartling.signal_handlers.logger.info"
    )

    admin_1 = WagtailUserFactory(
        username="admin_1",
        email="admin_1@example.com",
        is_superuser=True,
    )
    user_1 = WagtailUserFactory(
        username="user_1",
        email="user_1@example.com",
        is_superuser=False,
    )
    WagtailUserFactory(
        username="admin_2",
        email="admin_2@example.com",
        is_superuser=True,
    )

    ta_group = TranslationApproverGroupFactory()
    ta_group.user_set.add(admin_1)
    ta_group.user_set.add(user_1)

    mock_job = mocker.MagicMock(spec=Job)
    mock_job.name = "Test Job"
    mock_job.pk = 9876
    mock_source = mocker.Mock(name="test-source")
    mock_job.translation_source.get_source_instance.return_value = mock_source

    mock_translation_fr = mocker.MagicMock(spec=Translation, name="mock-translation-fr")
    mock_translation_fr.target_locale.language_code = "fr"
    mock_translation_fr_CA = mocker.MagicMock(
        spec=Translation, name="mock-translation-fr-CA"
    )
    mock_translation_fr_CA.target_locale.language_code = "fr-CA"

    mock_send_mail = mocker.patch(
        "wagtail_localize_smartling.signal_handlers.send_mail"
    )

    translation_import_successful.send(
        sender=Job,
        instance=mock_job,
        translations_imported=[mock_translation_fr, mock_translation_fr_CA],
    )
    assert mock_send_mail.call_count == 1
    assert (
        mock_send_mail.call_args[1]["subject"]
        == "New translations imported from Smartling"
    )
    assert mock_send_mail.call_args[1]["from_email"] == "from@example.com"
    assert mock_send_mail.call_args[1]["recipient_list"] == [
        "admin_1@example.com",
        "user_1@example.com",
    ]

    for expected_string in [
        "ACTION REQUIRED: Translations have been synced back from Smartling and need to be published.",
        "They are for Job 'Test Job'",
    ]:
        assert expected_string in mock_send_mail.call_args[1]["message"]
    assert (
        mock_logger_info.call_args_list[0][0][0]
        == "Translation-imported notification sent to 2 users"
    )


@pytest.mark.parametrize(
    "translation_target_locales",
    (
        [
            Mock(language_code="fr"),
            Mock(language_code="fr-CA"),
            Mock(language_code="de"),
        ],
        [
            Mock(language_code="fr"),
        ],
    ),
)
def test_notification_body_rendering(translation_target_locales):
    email_body = render_to_string(
        template_name="wagtail_localize_smartling/admin/email/notifications/translations_imported__body.txt",
        context={
            "job_name": "TEST JOB",
            "translation_source_name": "TS NAME",
            "translation_target_locales": translation_target_locales,
        },
    )
    if len(translation_target_locales) == 1:
        assert "It is for Job 'TEST JOB' for 'TS NAME" in email_body
    else:
        assert "They are for Job 'TEST JOB' for 'TS NAME" in email_body


def test_notify_of_imported_translations__no_group_members(mocker):
    mock_logger_warning = mocker.patch(
        "wagtail_localize_smartling.signal_handlers.logger.warning"
    )

    mock_job = mocker.MagicMock(spec=Job)
    mock_job.name = "Test Job"
    mock_job.pk = 9876
    mock_source = mocker.Mock(name="test-source")
    mock_job.translation_source.get_source_instance.return_value = mock_source

    mock_translation_fr = mocker.MagicMock(spec=Translation, name="mock-translation-fr")
    mock_translation_fr.target_locale.language_code = "fr"
    mock_translation_fr_CA = mocker.MagicMock(
        spec=Translation, name="mock-translation-fr-CA"
    )
    mock_translation_fr_CA.target_locale.language_code = "fr-CA"

    mock_send_mail = mocker.patch(
        "wagtail_localize_smartling.signal_handlers.send_mail"
    )

    translation_import_successful.send(
        sender=Job,
        instance=mock_job,
        translations_imported=[mock_translation_fr, mock_translation_fr_CA],
    )
    assert mock_send_mail.call_count == 0
    assert mock_logger_warning.call_args_list[0][0][0] == (
        "Unable to send translation-imported email notifications: "
        "no Translation Approvers in system"
    )


def test_create_landed_translation_task__task_created(
    mocker,
    smartling_settings,
):
    smartling_settings.ADD_APPROVAL_TASK_TO_DASHBOARD = True
    mock_create_from_source_and_translation = mocker.patch(
        "wagtail_localize_smartling.models.LandedTranslationTask.objects.create_from_source_and_translation"
    )

    mock_job = mocker.MagicMock(spec=Job)
    mock_translation = mocker.MagicMock(spec=Translation)
    mock_source_instance = mocker.Mock(name="test-source")
    mock_job.translation_source.get_source_instance.return_value = mock_source_instance

    create_landed_translation_task(
        sender=Job,
        instance=mock_job,
        translation=mock_translation,
    )

    mock_create_from_source_and_translation.assert_called_once_with(
        source_object=mock_source_instance,
        translated_locale=mock_translation.target_locale,
    )


def test_create_landed_translation_task__disabled_by_settings(
    mocker,
    smartling_settings,
):
    smartling_settings.ADD_APPROVAL_TASK_TO_DASHBOARD = False

    mock_logger_debug = mocker.patch(
        "wagtail_localize_smartling.signal_handlers.logger.debug"
    )
    mock_create_from_source_and_translation = mocker.patch(
        "wagtail_localize_smartling.models.LandedTranslationTask.objects.create_from_source_and_translation"
    )

    mock_job = mocker.MagicMock(spec=Job)
    mock_translation = mocker.MagicMock(spec=Translation)

    create_landed_translation_task(
        sender=Job, instance=mock_job, translation=mock_translation
    )

    mock_logger_debug.assert_called_once_with(
        "Creation of a landed-translation task is disabled by settings"
    )
    assert not mock_create_from_source_and_translation.called


def test__close_translation_landed_task__happy_path(mocker):
    mock_task = mocker.MagicMock(spec=LandedTranslationTask)
    mock_task.complete = mocker.MagicMock()

    mock_tasks_filter = mocker.patch(
        "wagtail_localize_smartling.signal_handlers.LandedTranslationTask.objects.filter",
        return_value=[mock_task],
    )
    mock_instance = mocker.Mock()
    mock_instance.pk = 1234
    mock_content_type = mocker.patch(
        "wagtail_localize_smartling.signal_handlers.ContentType.objects.get_for_model",
        return_value=mocker.Mock(),
    )

    _close_translation_landed_task(mock_instance)

    mock_content_type.assert_called_once_with(mock_instance)
    mock_tasks_filter.assert_called_once_with(
        content_type=mock_content_type.return_value,
        object_id=1234,
    )
    mock_task.complete.assert_called_once()


def test__close_translation_landed_task__no_tasks_found(mocker):
    mock_tasks_filter = mocker.patch(
        "wagtail_localize_smartling.signal_handlers.LandedTranslationTask.objects.filter",
        return_value=[],
    )
    mock_instance = mocker.Mock()
    mock_instance.pk = 5432
    mock_content_type = mocker.patch(
        "wagtail_localize_smartling.signal_handlers.ContentType.objects.get_for_model",
        return_value=mocker.Mock(),
    )

    _close_translation_landed_task(mock_instance)

    mock_content_type.assert_called_once_with(mock_instance)
    mock_tasks_filter.assert_called_once_with(
        content_type=mock_content_type.return_value,
        object_id=5432,
    )


@pytest.mark.parametrize("fake_a_page", [True, False])
def test_close_translation_landed_task_on_page_published(fake_a_page, mocker):
    if fake_a_page:
        instance = Page()
    else:
        instance = User()

    mock_close_translation_landed_task = mocker.patch(
        "wagtail_localize_smartling.signal_handlers._close_translation_landed_task"
    )

    close_translation_landed_task_on_page_published(
        sender=Page,
        instance=instance,
    )

    if fake_a_page:
        mock_close_translation_landed_task.assert_called_once_with(instance)
    else:
        assert not mock_close_translation_landed_task.called


@pytest.mark.parametrize("is_a_snippet", [True, False])
def test_close_translation_landed_task_on_snippet_published(is_a_snippet, mocker):
    mock_instance = mocker.Mock()
    mock_close_translation_landed_task = mocker.patch(
        "wagtail_localize_smartling.signal_handlers._close_translation_landed_task"
    )
    mock_model_is_registered_as_snippet = mocker.patch(
        "wagtail_localize_smartling.signal_handlers._model_is_registered_as_snippet",
        return_value=is_a_snippet,
    )

    close_translation_landed_task_on_snippet_published(
        sender=mock_instance.__class__,
        instance=mock_instance,
    )

    mock_model_is_registered_as_snippet.assert_called_once_with(mock_instance)
    if is_a_snippet:
        mock_close_translation_landed_task.assert_called_once_with(mock_instance)
    else:
        assert not mock_close_translation_landed_task.called


@pytest.mark.parametrize("is_a_snippet", [True, False])
def test_model_is_registered_as_snippet(is_a_snippet, mocker):
    model_1 = mocker.Mock("model_1")
    model_2 = mocker.Mock("model_2")
    model_3 = mocker.Mock("model_3")
    if is_a_snippet:
        get_snippet_models_retval = [model_1, model_2, model_3]
    else:
        get_snippet_models_retval = [model_1, model_3]

    mocker.patch(
        "wagtail_localize_smartling.signal_handlers.get_snippet_models",
        return_value=get_snippet_models_retval,
    )

    assert _model_is_registered_as_snippet(model_2) == is_a_snippet
