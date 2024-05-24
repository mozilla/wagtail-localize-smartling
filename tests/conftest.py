import json

from pathlib import Path
from urllib.parse import quote

import pytest

from django.test.client import Client
from wagtail.coreutils import get_supported_content_language_variant
from wagtail.models import Locale, Page
from wagtail_localize.models import LocaleSynchronization
from wagtail_localize_smartling.api.client import client
from wagtail_localize_smartling.api.types import (
    AuthenticateResponseData,
    GetProjectDetailsResponseData,
    TargetLocaleData,
)
from wagtail_localize_smartling.models import Project

from tests.testapp.factories import SuperUserFactory


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
def superuser(client: Client):
    return SuperUserFactory()


@pytest.fixture()
def dummy_smartling_settings(settings, responses):
    project_id = "dummy_smartling_project_id"
    user_identifier = "dummy_smartling_user"
    user_secret = "dummy_smartling_secret"  # noqa: S105

    # Django settings
    settings.WAGTAIL_LOCALIZE_SMARTLING = {
        "PROJECT_ID": project_id,
        "USER_IDENTIFIER": user_identifier,
        "USER_SECRET": user_secret,
    }

    # Mock API responses that always get used for authentication and getting
    # project details
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

    # Reset the API client so that the authenticate response gets consumed
    client.access_token = None
    client.refresh_token = None

    # Reset cached value of Project.get_current() so the project details
    # response always gets consumed
    Project.get_current.cache_clear()

    return settings.WAGTAIL_LOCALIZE_SMARTLING
