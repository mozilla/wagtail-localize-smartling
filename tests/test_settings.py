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
    assert smartling_settings.LOCALE_TO_SMARTLING_LOCALE == {}
    assert smartling_settings.SMARTLING_LOCALE_TO_LOCALE == {}
    assert smartling_settings.REFORMAT_LANGUAGE_CODES is True
    assert smartling_settings.JOB_NAME_PREFIX is None
    assert smartling_settings.JOB_DESCRIPTION_CALLBACK is None
    assert smartling_settings.VISUAL_CONTEXT_CALLBACK is None


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


@override_settings(
    WAGTAIL_LOCALIZE_SMARTLING={
        **REQUIRED_SETTINGS,
        "JOB_DESCRIPTION_CALLBACK": "testapp.settings.job_description_callback",
    }
)
def test_job_description_callback():
    smartling_settings = _init_settings()
    fn = smartling_settings.JOB_DESCRIPTION_CALLBACK
    assert callable(fn)
    assert fn.__name__ == "job_description_callback"


@override_settings(
    WAGTAIL_LOCALIZE_SMARTLING={
        **REQUIRED_SETTINGS,
        "JOB_DESCRIPTION_CALLBACK": 123,
    }
)
def test_invalid_job_description_callback_signature():
    smartling_settings = _init_settings()
    assert smartling_settings.JOB_DESCRIPTION_CALLBACK is None


@override_settings(
    WAGTAIL_LOCALIZE_SMARTLING={
        **REQUIRED_SETTINGS,
        "VISUAL_CONTEXT_CALLBACK": "testapp.settings.visual_context_callback",
    }
)
def test_visual_context_callback(smartling_job):
    smartling_settings = _init_settings()
    fn = smartling_settings.VISUAL_CONTEXT_CALLBACK
    assert callable(fn)
    assert fn.__name__ == "visual_context_callback"
    assert fn(smartling_job) == (
        "https://example.com/path/to/page/",
        "<html><body>test</body></html>",
    )


@override_settings(
    WAGTAIL_LOCALIZE_SMARTLING={
        **REQUIRED_SETTINGS,
        "VISUAL_CONTEXT_CALLBACK": 123,
    }
)
def test_invalid_visual_context_callback_signature():
    smartling_settings = _init_settings()
    assert smartling_settings.VISUAL_CONTEXT_CALLBACK is None


@override_settings(
    WAGTAIL_LOCALIZE_SMARTLING={
        **REQUIRED_SETTINGS,
        "REFORMAT_LANGUAGE_CODES": False,
    }
)
def test_reformat_language_codes():
    smartling_settings = _init_settings()
    assert smartling_settings.REFORMAT_LANGUAGE_CODES is False


@override_settings(
    WAGTAIL_LOCALIZE_SMARTLING={
        **REQUIRED_SETTINGS,
        "JOB_NAME_PREFIX": "Test prefix",
    }
)
def test_job_name_prefix():
    smartling_settings = _init_settings()
    assert smartling_settings.JOB_NAME_PREFIX == "Test prefix"
