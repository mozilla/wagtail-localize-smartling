from unittest import mock

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


@pytest.mark.parametrize(
    "url, expected",
    (
        ("https://example.com/test/path/here", "example-com-test-path-here.html"),
        ("https://example.com/test/path/here/", "example-com-test-path-here.html"),
        ("https://www.example.com/test/", "www-example-com-test.html"),
        (
            "https://example.com/fr-CA/test/path/to/a/page/here",
            "example-com-fr-ca-test-path-to-a-page-here.html",
        ),
        ("", ""),
        ("https://example.com/", "example-com.html"),
        (
            "https://example.com/test/path/here" + "/here" * 60,  # well over 300 chars
            "example-com-test-path-here"
            + "-here" * 45
            + ".html",  # 26 + 45*5 + 5 = 256 chars == func's default max_length
        ),
    ),
)
@pytest.mark.django_db
def test_get_filename_for_visual_context(url, expected):
    assert utils.get_filename_for_visual_context(url) == expected


@pytest.mark.django_db
def test_get_snippet_admin_url():
    snippet = mock.Mock()
    snippet._meta.app_label = "testapp"
    snippet._meta.model_name = "testmodel"
    snippet.pk = 1

    expected_url = "/admin/snippets/testapp/testmodel/edit/1/"
    assert utils.get_snippet_admin_url(snippet) == expected_url
