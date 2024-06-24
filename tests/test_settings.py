import pytest

from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from wagtail_localize_smartling.settings import _init_settings


pytestmark = pytest.mark.django_db


@override_settings(
    WAGTAIL_LOCALIZE_SMARTLING={
        "PROJECT_ID": "test_project_id",
        "USER_IDENTIFIER": "test_user_identifier",
        "USER_SECRET": "test_user_secret",
        "REQUIRED": True,
        "ENVIRONMENT": "staging",
        "API_TIMEOUT_SECONDS": 10.0,
        "LOCALE_MAPPING_CALLBACK": lambda x: x,
    }
)
def test_settings():
    smartling_settings = _init_settings()
    assert smartling_settings.REQUIRED is True
    assert smartling_settings.ENVIRONMENT == "staging"
    assert smartling_settings.API_TIMEOUT_SECONDS == 10.0
    assert callable(smartling_settings.LOCALE_MAPPING_CALLBACK)


@override_settings(
    WAGTAIL_LOCALIZE_SMARTLING={
        "PROJECT_ID": "",
        "USER_IDENTIFIER": "",
        "USER_SECRET": "",
    }
)
def test_missing_required_fields(settings):
    with pytest.raises(ImproperlyConfigured):
        _init_settings()


@override_settings(
    WAGTAIL_LOCALIZE_SMARTLING={
        "PROJECT_ID": "test_project_id",
        "USER_IDENTIFIER": "test_user_identifier",
        "USER_SECRET": "test_user_secret",
        "ENVIRONMENT": "invalid_env",
    }
)
def test_invalid_environment_value(settings):
    with pytest.raises(ImproperlyConfigured):
        _init_settings()


@override_settings(
    WAGTAIL_LOCALIZE_SMARTLING={
        "PROJECT_ID": "test_project_id",
        "USER_IDENTIFIER": "test_user_identifier",
        "USER_SECRET": "test_user_secret",
        "API_TIMEOUT_SECONDS": "non_numeric_value",
    }
)
def test_non_numeric_api_timeout_seconds(settings):
    from django.core.exceptions import ImproperlyConfigured

    with pytest.raises(ImproperlyConfigured):
        _init_settings()
