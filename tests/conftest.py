import dataclasses
import json

from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import quote

import pytest

from django.contrib.auth.models import Permission
from wagtail.coreutils import get_supported_content_language_variant
from wagtail.models import Locale, Page
from wagtail_localize.models import LocaleSynchronization

from wagtail_localize_smartling.api.client import client
from wagtail_localize_smartling.api.types import (
    AddVisualContextToJobResponseData,
    AuthenticateResponseData,
    CreateBatchForJobResponseData,
    GetProjectDetailsResponseData,
    TargetLocaleData,
    UploadFileToBatchResponseData,
)
from wagtail_localize_smartling.models import Project

from testapp.factories import UserFactory


if TYPE_CHECKING:
    from wagtail_localize_smartling.models import Job


@pytest.fixture(autouse=True)
def temporary_media_dir(settings, tmp_path: Path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


@pytest.fixture(autouse=True)
def locales(settings):
    default_locale = Locale.objects.get(
        language_code=get_supported_content_language_variant(settings.LANGUAGE_CODE)
    )

    locales = []
    for wagtail_language_code, _ in settings.WAGTAIL_CONTENT_LANGUAGES:
        if wagtail_language_code == default_locale.language_code:
            continue
        locale = Locale.objects.create(language_code=wagtail_language_code)
        locales.append(locale)
        LocaleSynchronization.objects.create(locale=locale, sync_from=default_locale)

    return locales


@pytest.fixture()
def root_page():
    return Page.objects.filter(depth=1).get()


@pytest.fixture()
def superuser():
    return UserFactory(is_superuser=True)


@pytest.fixture()
def regular_user():
    user = UserFactory(is_superuser=False)
    user.user_permissions.add(Permission.objects.get(codename="access_admin"))
    return user


@pytest.fixture()
def smartling_reporter():
    user = UserFactory(is_superuser=False)
    user.user_permissions.add(Permission.objects.get(codename="access_admin"))
    user.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="wagtail_localize_smartling", codename="view_job"
        )
    )
    return user


@pytest.fixture()
def smartling_auth(responses):
    # Mock API response for authentication
    responses.post(
        "https://api.smartling.com/auth-api/v2/authenticate",
        body=json.dumps(
            {
                "response": {
                    "code": "SUCCESS",
                    "data": AuthenticateResponseData(
                        accessToken="dummyaccesstoken",
                        expiresIn=10**6,
                        refreshExpiresIn=10**7,
                        refreshToken="dummyrefreshtoken",
                        tokenType="Bearer",
                    ),
                }
            }
        ),
    )

    # Reset the API client so that the authenticate response gets consumed
    client.access_token = None
    client.refresh_token = None


@pytest.fixture()
def smartling_project(responses, settings, smartling_auth):
    # Mock API request for retreiving project details
    project_id = settings.WAGTAIL_LOCALIZE_SMARTLING["PROJECT_ID"]
    responses.get(
        f"https://api.smartling.com/projects-api/v2/projects/{quote(project_id)}?includeDisabledLocales=true",
        body=json.dumps(
            {
                "response": {
                    "code": "SUCCESS",
                    "data": GetProjectDetailsResponseData(
                        accountUid="dummy_account_uid",
                        archived=False,
                        projectId=project_id,
                        projectName="Dummy project",
                        projectTypeCode="APPLICATION_RESOURCES",
                        sourceLocaleDescription="English (United States) [en-US]",
                        sourceLocaleId="en-US",
                        targetLocales=[
                            TargetLocaleData(
                                description="French (International) [fr]",
                                localeId="fr",
                                enabled=True,
                            ),
                            TargetLocaleData(
                                description="German (International) [de]",
                                localeId="de",
                                enabled=True,
                            ),
                        ],
                    ),
                },
            }
        ),
    )

    # Reset Project.get_current() cache so the response always gets consumed
    Project.get_current.cache_clear()
    return Project.get_current()


@pytest.fixture()
def smartling_add_visual_context(responses, settings, smartling_auth):
    # Mock API request for sending visual context
    project_id = settings.WAGTAIL_LOCALIZE_SMARTLING["PROJECT_ID"]
    responses.assert_all_requests_are_fired = False
    responses.add(
        method="POST",
        url=f"https://api.smartling.com/context-api/v2/projects/{quote(project_id)}/contexts/upload-and-match-async",
        body=json.dumps(
            {
                "response": {
                    "code": "SUCCESS",
                    "data": AddVisualContextToJobResponseData(
                        processUid="dummy_process_uid",
                    ),
                },
            }
        ),
    )


@pytest.fixture()
def smartling_add_visual_context__error_response(responses, settings, smartling_auth):
    # Mock API request for sending visual context
    project_id = settings.WAGTAIL_LOCALIZE_SMARTLING["PROJECT_ID"]
    responses.assert_all_requests_are_fired = False
    responses.add(
        method="POST",
        url=f"https://api.smartling.com/context-api/v2/projects/{quote(project_id)}/contexts/upload-and-match-async",
        body=json.dumps(
            {
                "response": {
                    "code": "VALIDATION_ERROR",
                    "data": [
                        {
                            "key": "some key",
                            "message": "some message",
                            "details": "some details",
                        }
                    ],
                },
            }
        ),
    )


@pytest.fixture()
def smartling_create_batch_for_job(responses, settings, smartling_auth):
    # Mock API request for creating a batch
    project_id = settings.WAGTAIL_LOCALIZE_SMARTLING["PROJECT_ID"]
    responses.add(
        method="POST",
        url=f"https://api.smartling.com/job-batches-api/v2/projects/{quote(project_id)}/batches",
        body=json.dumps(
            {
                "response": {
                    "code": "SUCCESS",
                    "data": CreateBatchForJobResponseData(
                        batchUid="dummy_batch_uid",
                    ),
                },
            }
        ),
    )


@pytest.fixture()
def smartling_create_batch_for_job__error_response(responses, settings, smartling_auth):
    # Mock API request for creating a batch
    project_id = settings.WAGTAIL_LOCALIZE_SMARTLING["PROJECT_ID"]
    responses.add(
        method="POST",
        url=f"https://api.smartling.com/job-batches-api/v2/projects/{quote(project_id)}/batches",
        body=json.dumps(
            {
                "response": {
                    "code": "VALIDATION_ERROR",
                    "data": [
                        {
                            "key": "some batch key",
                            "message": "some batch message",
                            "details": "some batch details",
                        }
                    ],
                },
            }
        ),
    )


@pytest.fixture()
def smartling_upload_files_to_job_batch(responses, settings, smartling_auth):
    # Mock API request for uploading a file to a batch
    project_id = settings.WAGTAIL_LOCALIZE_SMARTLING["PROJECT_ID"]
    batch_uid = "test-batch-uid"
    responses.assert_all_requests_are_fired = False
    responses.add(
        method="POST",
        url=f"https://api.smartling.com/job-batches-api/v2/projects/{quote(project_id)}/batches/{batch_uid}/file",
        body=json.dumps(
            {
                "response": {
                    "code": "ACCEPTED",
                    "data": UploadFileToBatchResponseData(),
                },
            }
        ),
        status=202,
    )


@pytest.fixture()
def smartling_upload_files_to_job_batch__error_response(
    responses, settings, smartling_auth
):
    # Mock API request for uploading a file to a batch
    project_id = settings.WAGTAIL_LOCALIZE_SMARTLING["PROJECT_ID"]
    batch_uid = "test-batch-uid"
    responses.assert_all_requests_are_fired = False
    responses.add(
        method="POST",
        url=f"https://api.smartling.com/job-batches-api/v2/projects/{quote(project_id)}/batches/{batch_uid}/file",
        body=json.dumps(
            {
                "response": {
                    "code": "VALIDATION_ERROR",
                    "data": [
                        {
                            "key": "some upload key",
                            "message": "some upload message",
                            "details": "some upload details",
                        }
                    ],
                },
            }
        ),
        status=400,
    )


@pytest.fixture
def smartling_settings():
    """
    Fixture that allows patching of the SmartlingSettings singleton object per test
    """
    from wagtail_localize_smartling import settings

    # Unwrap the SimpleLazyObject to get the actual settings object
    original_settings = cast(settings.SmartlingSettings, settings.settings._wrapped)  # pyright: ignore[reportAttributeAccessIssue]

    # Create a mutable version of the settings object
    settings_dict = dataclasses.asdict(original_settings)

    class MutableSettings:
        __slots__ = tuple(settings_dict.keys())

    mutable_settings = MutableSettings()
    for k, v in settings_dict.items():
        setattr(mutable_settings, k, v)

    # Patch the SimpleLazyObject to use the mutable settings
    settings.settings._wrapped = mutable_settings  # pyright: ignore[reportAttributeAccessIssue]

    yield mutable_settings

    # Restore the original settings object
    settings.settings._wrapped = original_settings  # pyright: ignore[reportAttributeAccessIssue]


@pytest.fixture
def smartling_job(smartling_project, superuser, root_page) -> "Job":
    from django.utils import timezone
    from wagtail.models import Locale
    from wagtail_localize.models import Translation, TranslationSource

    from wagtail_localize_smartling.api.types import JobStatus
    from wagtail_localize_smartling.models import Job
    from wagtail_localize_smartling.utils import compute_content_hash

    from testapp.factories import InfoPageFactory

    # setup
    page = InfoPageFactory(parent=root_page, title="Component test page")
    translation_source, created = TranslationSource.get_or_create_from_instance(page)
    page_translation = Translation.objects.create(
        source=translation_source,
        target_locale=Locale.objects.get(language_code="fr"),
    )

    now = timezone.now()
    job = Job.objects.create(
        project=smartling_project,
        translation_source=translation_source,
        user=superuser,
        name=Job.get_default_name(translation_source, [page_translation]),
        description=Job.get_description(translation_source, [page_translation]),
        reference_number=Job.get_default_reference_number(
            translation_source, [page_translation]
        ),
        content_hash=compute_content_hash(translation_source.export_po()),
        first_synced_at=now,
        last_synced_at=now,
        status=JobStatus.DRAFT,
        translation_job_uid="job_to_be_cancelled",
    )
    job.translations.set([page_translation])

    return job
