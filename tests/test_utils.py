import pytest

from wagtail_localize_smartling import utils


@pytest.mark.parametrize(
    ("locale_id", "expected"),
    [
        ("en", "en"),
        ("FR", "fr-FR"),
        ("en-us", "en-US"),
        ("en-GB", "en-GB"),
    ],
)
@pytest.mark.django_db
def test_format_smartling_locale_id(
    locale_id,
    expected,
    smartling_settings,
):
    smartling_settings.LOCALE_TO_SMARTLING_LOCALE = {
        "FR": "fr-FR",
    }
    assert utils.format_smartling_locale_id(locale_id) == expected


@pytest.mark.parametrize(
    (
        "locale_id",
        "reformat",
        "expected",
    ),
    [
        ("en", True, "en"),
        ("en-US", True, "en-us"),
        ("en-US", False, "en-US"),
        ("fr-FR", True, "fr"),
        ("fr-FR", False, "FR"),
    ],
)
@pytest.mark.django_db
def test_format_wagtail_locale_id(locale_id, expected, reformat, smartling_settings):
    smartling_settings.REFORMAT_LANGUAGE_CODES = reformat
    smartling_settings.SMARTLING_LOCALE_TO_LOCALE = {
        "fr-FR": "FR",
    }
    assert utils.format_wagtail_locale_id(locale_id) == expected
