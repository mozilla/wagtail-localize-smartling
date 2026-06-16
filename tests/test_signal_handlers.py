# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

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
    assert individual_translation_imported.receivers[0][1] == create_landed_translation_task


def test_translation_import_successful_is_connected_to_the_expected_handlers():
    assert len(translation_import_successful.receivers) == 1
    assert translation_import_successful.receivers[0][1] == send_email_notification_upon_overall_translation_import


def _mock_translation(mocker, *, language_code, language_name, edit_path):
    t = mocker.MagicMock(spec=Translation, name=f"mock-translation-{language_code}")
    t.target_locale.language_code = language_code
    t.target_locale.language_name = language_name
    t.get_target_instance_edit_url.return_value = edit_path
    return t


@override_settings(
    DEFAULT_FROM_EMAIL="from@example.com",
    WAGTAILADMIN_BASE_URL="https://cms.example.com",
)
def test_notify_of_imported_translations__happy_path(mocker):
    mock_logger_info = mocker.patch("wagtail_localize_smartling.signal_handlers.logger.info")

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
    submitter = WagtailUserFactory(
        username="submitter",
        email="submitter@example.com",
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
    mock_job.user = submitter
    mock_job.user_id = submitter.pk
    mock_source = mocker.Mock(name="test-source")
    mock_job.translation_source.get_source_instance.return_value = mock_source

    mock_translation_fr = _mock_translation(
        mocker,
        language_code="fr",
        language_name="French",
        edit_path="/admin/pages/12/edit/",
    )
    mock_translation_fr_CA = _mock_translation(
        mocker,
        language_code="fr-CA",
        language_name="French (Canada)",
        edit_path="/admin/pages/13/edit/",
    )

    mock_send_mail = mocker.patch("wagtail_localize_smartling.signal_handlers.send_mail")

    translation_import_successful.send(
        sender=Job,
        instance=mock_job,
        translations_imported=[mock_translation_fr, mock_translation_fr_CA],
    )
    assert mock_send_mail.call_count == 1
    assert mock_send_mail.call_args[1]["subject"] == "New translations imported from Smartling"
    assert mock_send_mail.call_args[1]["from_email"] == "from@example.com"
    assert mock_send_mail.call_args[1]["recipient_list"] == [
        "admin_1@example.com",
        "user_1@example.com",
        "submitter@example.com",
    ]

    message = mock_send_mail.call_args[1]["message"]
    for expected_string in [
        "ACTION REQUIRED: Translations have been synced back from Smartling and need to be published.",
        "They are for Job 'Test Job'",
        "* French - https://cms.example.com/admin/pages/12/edit/",
        "* French (Canada) - https://cms.example.com/admin/pages/13/edit/",
    ]:
        assert expected_string in message
    assert mock_logger_info.call_args_list[0][0][0] == "Translation-imported notification sent to 3 users"


@override_settings(
    DEFAULT_FROM_EMAIL="from@example.com",
    WAGTAILADMIN_BASE_URL="https://cms.example.com",
)
def test_notify_of_imported_translations__submitter_already_in_group_dedupes(mocker):
    # If the Job submitter is also a Translation Approver, they should
    # only receive the email once.
    submitter = WagtailUserFactory(
        username="submitter",
        email="approver@example.com",
        is_superuser=False,
    )

    ta_group = TranslationApproverGroupFactory()
    ta_group.user_set.add(submitter)

    mock_job = mocker.MagicMock(spec=Job)
    mock_job.name = "Test Job"
    mock_job.user = submitter
    mock_job.user_id = submitter.pk
    mock_job.translation_source.get_source_instance.return_value = mocker.Mock(name="src")

    mock_translation = _mock_translation(
        mocker,
        language_code="fr",
        language_name="French",
        edit_path="/admin/pages/1/edit/",
    )

    mock_send_mail = mocker.patch("wagtail_localize_smartling.signal_handlers.send_mail")
    translation_import_successful.send(
        sender=Job,
        instance=mock_job,
        translations_imported=[mock_translation],
    )

    assert mock_send_mail.call_args[1]["recipient_list"] == ["approver@example.com"]


@override_settings(
    DEFAULT_FROM_EMAIL="from@example.com",
    WAGTAILADMIN_BASE_URL="https://cms.example.com",
)
def test_notify_of_imported_translations__submitter_only_when_group_missing(mocker):
    # When the Translation Approver group doesn't exist, the submitter
    # alone should still receive the email — previously this branch
    # returned early.
    #
    # Note: a data migration creates the group on install, so we have to
    # explicitly delete it to exercise the missing-group path.
    from django.contrib.auth.models import Group
    Group.objects.filter(name="Translation approver").delete()

    mock_logger_warning = mocker.patch("wagtail_localize_smartling.signal_handlers.logger.warning")

    submitter = WagtailUserFactory(
        username="submitter",
        email="submitter@example.com",
    )

    mock_job = mocker.MagicMock(spec=Job)
    mock_job.name = "Test Job"
    mock_job.user = submitter
    mock_job.user_id = submitter.pk
    mock_job.translation_source.get_source_instance.return_value = mocker.Mock(name="src")

    mock_translation = _mock_translation(
        mocker,
        language_code="fr",
        language_name="French",
        edit_path="/admin/pages/1/edit/",
    )

    mock_send_mail = mocker.patch("wagtail_localize_smartling.signal_handlers.send_mail")
    translation_import_successful.send(
        sender=Job,
        instance=mock_job,
        translations_imported=[mock_translation],
    )

    assert mock_send_mail.call_args[1]["recipient_list"] == ["submitter@example.com"]
    # The missing-group warning still fires.
    assert any(
        "Could not find the Translation approver" in call.args[0]
        for call in mock_logger_warning.call_args_list
    )


@pytest.mark.parametrize(
    "translation_targets",
    (
        [
            {"locale_name": "French", "edit_url": "https://cms.example.com/admin/pages/1/edit/"},
            {"locale_name": "French (Canada)", "edit_url": "https://cms.example.com/admin/pages/2/edit/"},
            {"locale_name": "German", "edit_url": "https://cms.example.com/admin/pages/3/edit/"},
        ],
        [
            {"locale_name": "French", "edit_url": "https://cms.example.com/admin/pages/1/edit/"},
        ],
    ),
)
def test_notification_body_rendering(translation_targets):
    email_body = render_to_string(
        template_name="wagtail_localize_smartling/admin/email/notifications/translations_imported__body.txt",
        context={
            "job_name": "TEST JOB",
            "translation_source_name": "TS NAME",
            "translation_targets": translation_targets,
        },
    )
    if len(translation_targets) == 1:
        assert "It is for Job 'TEST JOB' for 'TS NAME" in email_body
    else:
        assert "They are for Job 'TEST JOB' for 'TS NAME" in email_body

    for target in translation_targets:
        assert f"* {target['locale_name']} - {target['edit_url']}" in email_body


def test_notification_body_rendering__no_edit_url():
    # If we couldn't resolve a target's edit URL (deleted instance,
    # whatever), the locale name still appears but the dash + URL is
    # suppressed.
    email_body = render_to_string(
        template_name="wagtail_localize_smartling/admin/email/notifications/translations_imported__body.txt",
        context={
            "job_name": "TEST JOB",
            "translation_source_name": "TS NAME",
            "translation_targets": [
                {"locale_name": "French", "edit_url": ""},
            ],
        },
    )
    assert "* French" in email_body
    assert "* French - " not in email_body


def test_notify_of_imported_translations__no_group_members_and_no_submitter(mocker):
    # No approver group members + no submitter on the Job → nothing to send.
    mock_logger_warning = mocker.patch("wagtail_localize_smartling.signal_handlers.logger.warning")
    TranslationApproverGroupFactory()  # Group exists but is empty.

    mock_job = mocker.MagicMock(spec=Job)
    mock_job.name = "Test Job"
    mock_job.pk = 9876
    mock_job.user = None
    mock_job.user_id = None
    mock_source = mocker.Mock(name="test-source")
    mock_job.translation_source.get_source_instance.return_value = mock_source

    mock_translation_fr = _mock_translation(
        mocker,
        language_code="fr",
        language_name="French",
        edit_path="/admin/pages/1/edit/",
    )

    mock_send_mail = mocker.patch("wagtail_localize_smartling.signal_handlers.send_mail")

    translation_import_successful.send(
        sender=Job,
        instance=mock_job,
        translations_imported=[mock_translation_fr],
    )
    assert mock_send_mail.call_count == 0
    assert any(
        "no members of the Translation approver group in the system "
        "and no Job submitter email available" in call.args[0]
        for call in mock_logger_warning.call_args_list
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

    mock_logger_debug = mocker.patch("wagtail_localize_smartling.signal_handlers.logger.debug")
    mock_create_from_source_and_translation = mocker.patch(
        "wagtail_localize_smartling.models.LandedTranslationTask.objects.create_from_source_and_translation"
    )

    mock_job = mocker.MagicMock(spec=Job)
    mock_translation = mocker.MagicMock(spec=Translation)

    create_landed_translation_task(sender=Job, instance=mock_job, translation=mock_translation)

    mock_logger_debug.assert_called_once_with("Creation of a landed-translation task is disabled by settings")
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

    mock_model_is_registered_as_snippet.assert_called_once_with(mock_instance.__class__)
    if is_a_snippet:
        mock_close_translation_landed_task.assert_called_once_with(mock_instance)
    else:
        assert not mock_close_translation_landed_task.called


def test_close_translation_landed_task_on_snippet_published__skips_when_raw(mocker):
    mock_instance = mocker.Mock()
    mock_close_translation_landed_task = mocker.patch(
        "wagtail_localize_smartling.signal_handlers._close_translation_landed_task"
    )
    mock_model_is_registered_as_snippet = mocker.patch(
        "wagtail_localize_smartling.signal_handlers._model_is_registered_as_snippet"
    )
    mock_logger_debug = mocker.patch("wagtail_localize_smartling.signal_handlers.logger.debug")

    close_translation_landed_task_on_snippet_published(
        sender=mock_instance.__class__,
        instance=mock_instance,
        raw=True,
    )

    assert not mock_model_is_registered_as_snippet.called
    assert not mock_close_translation_landed_task.called
    mock_logger_debug.assert_called_once()


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
