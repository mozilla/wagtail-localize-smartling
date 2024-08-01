import dataclasses
import json

from pathlib import Path
from typing import cast
from urllib.parse import quote

import pytest

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

from testapp.factories import UserFactory


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
