import dataclasses
import json

from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import quote
from zipfile import ZipFile

import pytest

from django.contrib.auth.models import Group, Permission
from responses import matchers
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
    default_locale = Locale.objects.get(language_code=get_supported_content_language_variant(settings.LANGUAGE_CODE))

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
def editor_user():
    """
    Someone who edits pages and sends them for translation, with no more
    permission than installing this app gives such a group.
    """
    group = (
        Group.objects.filter(
            permissions__content_type__app_label="wagtail_localize",
            permissions__codename="submit_translation",
        )
        .filter(
            permissions__content_type__app_label="wagtail_localize_smartling",
            permissions__codename="change_order",
        )
        .distinct()
        # Both of Wagtail's default groups match; pick the same one every run.
        .order_by("pk")
        .first()
    )
    assert group is not None, "No group holds both submit_translation and change_order"

    user = UserFactory(is_superuser=False)
    user.groups.add(group)
    return user


@pytest.fixture()
def smartling_reporter():
    user = UserFactory(is_superuser=False)
    user.user_permissions.add(Permission.objects.get(codename="access_admin"))
    user.user_permissions.add(
        Permission.objects.get(content_type__app_label="wagtail_localize_smartling", codename="view_job")
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


CREATE_JOB_RESPONSE_BODY = {
    "response": {
        "code": "SUCCESS",
        "data": {
            "callbackMethod": None,
            "callbackUrl": None,
            "createdByUserUid": "dummy_user_uid",
            "createdDate": "2025-01-01T00:00:00Z",
            "description": "",
            "dueDate": None,
            "firstCompletedDate": None,
            "firstAuthorizedDate": None,
            "jobName": "Dummy job",
            "jobNumber": None,
            "jobStatus": "AWAITING_AUTHORIZATION",
            "lastCompletedDate": None,
            "lastAuthorizedDate": None,
            "modifiedByUserUid": None,
            "modifiedDate": "2025-01-01T00:00:00Z",
            "targetLocaleIds": ["fr", "de"],
            "translationJobUid": "dummy_translation_job_uid",
            "referenceNumber": None,
            "issues": None,
        },
    },
}


@pytest.fixture()
def smartling_create_job(responses, settings, smartling_auth):
    # Mock API request for creating a job
    project_id = settings.WAGTAIL_LOCALIZE_SMARTLING["PROJECT_ID"]
    responses.add(
        method="POST",
        url=f"https://api.smartling.com/jobs-api/v3/projects/{quote(project_id)}/jobs",
        body=json.dumps(CREATE_JOB_RESPONSE_BODY),
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
def smartling_get_batch_status(responses, settings, smartling_auth):
    # Mock API request for reading a batch's status and its per-file items
    project_id = settings.WAGTAIL_LOCALIZE_SMARTLING["PROJECT_ID"]

    def register_batch_status(*, batch_uid, status, files=None):
        data = {"status": status}
        if files is not None:
            data["files"] = files
        responses.add(
            method="GET",
            url=f"https://api.smartling.com/job-batches-api/v2/projects/{quote(project_id)}/batches/{batch_uid}",
            body=json.dumps({"response": {"code": "SUCCESS", "data": data}}),
        )

    return register_batch_status


@pytest.fixture()
def smartling_get_batch_status__not_found(responses, settings, smartling_auth):
    # Mock API request for a batch Smartling no longer has
    project_id = settings.WAGTAIL_LOCALIZE_SMARTLING["PROJECT_ID"]

    def register_missing_batch(*, batch_uid):
        responses.add(
            method="GET",
            url=f"https://api.smartling.com/job-batches-api/v2/projects/{quote(project_id)}/batches/{batch_uid}",
            body=json.dumps(
                {
                    "response": {
                        "code": "VALIDATION_ERROR",
                        "errors": [
                            {
                                "key": "batch.not.found",
                                "message": f"Batch with uid={batch_uid} and projectId={project_id} not found.",
                            }
                        ],
                    },
                }
            ),
            status=404,
        )

    return register_missing_batch


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
def smartling_upload_files_to_job_batch__error_response(responses, settings, smartling_auth):
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
    from wagtail_localize_smartling.models import Job, JobFile, Order, OrderItem
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
    order = Order.objects.create(name="Component test page", created_by=superuser, submitted_at=now)
    order_item = OrderItem.objects.create(order=order, translation_source=translation_source, added_by=superuser)
    order_item.translations.set([page_translation])

    job = Job.objects.create(
        project=smartling_project,
        order=order,
        translation_source=translation_source,
        user=superuser,
        name=Job.get_default_name(translation_source, [page_translation]),
        reference_number=Job.get_default_reference_number(translation_source, [page_translation]),
        content_hash=compute_content_hash(translation_source.export_po()),
        first_synced_at=now,
        last_synced_at=now,
        status=JobStatus.DRAFT,
        translation_job_uid="job_to_be_cancelled",
        file_uri="job_1_ts_1.po",
    )
    JobFile.objects.create(
        job=job,
        translation_source=translation_source,
        file_uri=f"job_{job.pk}_ts_{translation_source.pk}.po",
        content_hash=compute_content_hash(translation_source.export_po()),
    )
    job.translations.set([page_translation])
    job.description = Job.get_description_for_order(order, job=job)
    job.save(update_fields=["description"])

    return job


@pytest.fixture
def smartling_job_multi_locale(smartling_project, superuser, root_page) -> "Job":
    """A job with multiple target locales (fr and de)."""
    from django.utils import timezone
    from wagtail.models import Locale
    from wagtail_localize.models import Translation, TranslationSource

    from wagtail_localize_smartling.api.types import JobStatus
    from wagtail_localize_smartling.models import Job, JobFile, Order, OrderItem
    from wagtail_localize_smartling.utils import compute_content_hash

    from testapp.factories import InfoPageFactory

    page = InfoPageFactory(parent=root_page, title="Multi-locale test page")
    translation_source, _ = TranslationSource.get_or_create_from_instance(page)

    fr_translation = Translation.objects.create(
        source=translation_source,
        target_locale=Locale.objects.get(language_code="fr"),
    )
    de_translation = Translation.objects.create(
        source=translation_source,
        target_locale=Locale.objects.get(language_code="de"),
    )

    now = timezone.now()
    order = Order.objects.create(name="Multi-locale test page", created_by=superuser, submitted_at=now)
    order_item = OrderItem.objects.create(order=order, translation_source=translation_source, added_by=superuser)
    order_item.translations.set([fr_translation, de_translation])

    job = Job.objects.create(
        project=smartling_project,
        order=order,
        translation_source=translation_source,
        user=superuser,
        name=Job.get_default_name(translation_source, [fr_translation, de_translation]),
        reference_number=Job.get_default_reference_number(translation_source, [fr_translation, de_translation]),
        content_hash=compute_content_hash(translation_source.export_po()),
        first_synced_at=now,
        last_synced_at=now,
        status=JobStatus.IN_PROGRESS,
        translation_job_uid="multi_locale_job",
        file_uri="job_multi_locale.po",
    )
    JobFile.objects.create(
        job=job,
        translation_source=translation_source,
        file_uri=f"job_{job.pk}_ts_{translation_source.pk}.po",
        content_hash=compute_content_hash(translation_source.export_po()),
    )
    job.translations.set([fr_translation, de_translation])
    job.description = Job.get_description_for_order(order, job=job)
    job.save(update_fields=["description"])

    return job


def _file_uri_matchers(file_uri: str) -> list:
    """Match the request's fileUri when the caller named one."""
    if not file_uri:
        return []
    return [matchers.query_param_matcher({"fileUri": file_uri}, strict_match=False)]


@pytest.fixture()
def smartling_get_file_status(responses, settings, smartling_auth):
    """Mock API response for getting file status per locale."""
    project_id = settings.WAGTAIL_LOCALIZE_SMARTLING["PROJECT_ID"]

    def add_file_status_response(
        locale_id: str,
        total_strings: int = 10,
        completed_strings: int = 10,
        file_uri: str = "",
    ):
        responses.add(
            method="GET",
            url=f"https://api.smartling.com/files-api/v2/projects/{quote(project_id)}/locales/{locale_id}/file/status",
            body=json.dumps(
                {
                    "response": {
                        "code": "SUCCESS",
                        "data": {
                            "fileUri": file_uri or "test.po",
                            "totalStringCount": total_strings,
                            "totalWordCount": total_strings * 5,
                            "authorizedStringCount": total_strings,
                            "authorizedWordCount": total_strings * 5,
                            "completedStringCount": completed_strings,
                            "completedWordCount": completed_strings * 5,
                            "excludedStringCount": 0,
                            "excludedWordCount": 0,
                        },
                    },
                }
            ),
            match_querystring=False,
            match=_file_uri_matchers(file_uri),
        )

    return add_file_status_response


@pytest.fixture()
def smartling_download_translation_for_locale(responses, settings, smartling_auth):
    """Mock API response for downloading translation for a single locale."""
    project_id = settings.WAGTAIL_LOCALIZE_SMARTLING["PROJECT_ID"]

    def add_download_response(locale_id: str, po_content: str = "", file_uri: str = ""):
        if not po_content:
            po_content = """
msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"

msgid "Hello"
msgstr "Bonjour"
"""
        responses.add(
            method="GET",
            url=f"https://api.smartling.com/files-api/v2/projects/{quote(project_id)}/locales/{locale_id}/file",
            body=po_content.encode("utf-8"),
            status=200,
            match_querystring=False,
            match=_file_uri_matchers(file_uri),
        )

    return add_download_response


@pytest.fixture()
def smartling_translations_zip(responses, settings, smartling_auth):
    """Mock API response for the bulk download of one file's translations."""
    project_id = settings.WAGTAIL_LOCALIZE_SMARTLING["PROJECT_ID"]

    def add_translations_zip_response(*, file_uri: str, entries: dict):
        buffer = BytesIO()
        with ZipFile(buffer, "w") as archive:
            for name, content in entries.items():
                archive.writestr(name, content)

        responses.add(
            method="GET",
            url=f"https://api.smartling.com/files-api/v2/projects/{quote(project_id)}/locales/all/file/zip",
            body=buffer.getvalue(),
            status=200,
            match=_file_uri_matchers(file_uri),
        )

    return add_translations_zip_response


@pytest.fixture()
def smartling_add_locale_to_job(responses, settings, smartling_auth):
    """Mock API response for adding a locale to an existing job."""
    project_id = settings.WAGTAIL_LOCALIZE_SMARTLING["PROJECT_ID"]

    def add_locale_response(job_uid: str, locale_id: str):
        responses.add(
            method="POST",
            url=f"https://api.smartling.com/jobs-api/v3/projects/{quote(project_id)}/jobs/{job_uid}/locales/{locale_id}",
            body=json.dumps(
                {
                    "response": {
                        "code": "SUCCESS",
                        "data": None,
                    },
                }
            ),
        )

    return add_locale_response
