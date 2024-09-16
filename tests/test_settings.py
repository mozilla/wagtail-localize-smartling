import pytest

from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from wagtail_localize_smartling.settings import _init_settings


pytestmark = pytest.mark.django_db

REQUIRED_SETTINGS = {
    "PROJECT_ID": "test_project_id",
    "USER_IDENTIFIER": "test_user_identifier",
    "USER_SECRET": "test_user_secret",
}


@override_settings(
    WAGTAIL_LOCALIZE_SMARTLING={
        **REQUIRED_SETTINGS,
        "REQUIRED": True,
        "ENVIRONMENT": "staging",
        "API_TIMEOUT_SECONDS": 10.0,
    }
)
def test_settings():
    smartling_settings = _init_settings()
    assert smartling_settings.REQUIRED is True
    assert smartling_settings.ENVIRONMENT == "staging"
    assert smartling_settings.API_TIMEOUT_SECONDS == 10.0


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
        **REQUIRED_SETTINGS,
        "ENVIRONMENT": "invalid_env",
    }
)
def test_invalid_environment_value(settings):
    with pytest.raises(ImproperlyConfigured):
        _init_settings()


@override_settings(
    WAGTAIL_LOCALIZE_SMARTLING={
        **REQUIRED_SETTINGS,
        "API_TIMEOUT_SECONDS": "non_numeric_value",
    }
)
def test_non_numeric_api_timeout_seconds(settings):
    from django.core.exceptions import ImproperlyConfigured

    with pytest.raises(ImproperlyConfigured):
        _init_settings()


@override_settings(
    WAGTAIL_LOCALIZE_SMARTLING={
        **REQUIRED_SETTINGS,
        "LOCALE_MAPPING_CALLBACK": lambda x: x,
        "LOCALE_TO_SMARTLING_LOCALE": {"ro": "ro-RO"},
    }
)
def test_cannot_have_callback_and_mapping_dict(settings):
    with pytest.raises(ImproperlyConfigured):
        _init_settings()


@override_settings(
    WAGTAIL_LOCALIZE_SMARTLING={
        **REQUIRED_SETTINGS,
        "LOCALE_MAPPING_CALLBACK": "testapp.settings.map_project_locale_to_smartling",
    }
)
def test_locale_mapping_callback():
    smartling_settings = _init_settings()
    assert smartling_settings.LOCALE_TO_SMARTLING_LOCALE == {
        "de": "de",
        "en": "en",
        "fr": "fr-FR",
    }
    assert smartling_settings.SMARTLING_LOCALE_TO_LOCALE == {
        "de": "de",
        "en": "en",
        "fr-FR": "fr",
    }


@override_settings(
    WAGTAIL_LOCALIZE_SMARTLING={
        **REQUIRED_SETTINGS,
        "LOCALE_TO_SMARTLING_LOCALE": {"ro": "ro-RO"},
    }
)
def test_locale_map_dict():
    smartling_settings = _init_settings()
    assert smartling_settings.LOCALE_TO_SMARTLING_LOCALE == {
        "de": "de",
        "en": "en",
        "fr": "fr",
        "ro": "ro-RO",
    }
    assert smartling_settings.SMARTLING_LOCALE_TO_LOCALE == {
        "de": "de",
        "en": "en",
        "fr": "fr",
        "ro-RO": "ro",
    }
