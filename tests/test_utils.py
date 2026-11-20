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


@pytest.mark.parametrize(
    ("prefix", "expected"),
    [
        ("www.firefox.com", "www.firefox.com WNP 157"),
        ("", "WNP 157"),
        (None, "WNP 157"),
    ],
)
@pytest.mark.django_db
def test_prefixed_job_name(prefix, expected, smartling_settings):
    smartling_settings.JOB_NAME_PREFIX = prefix

    assert utils.prefixed_job_name("WNP 157") == expected


@pytest.mark.django_db
def test_max_order_name_length_reserves_the_prefix_and_a_resend_suffix(smartling_settings):
    smartling_settings.JOB_NAME_PREFIX = "job name prefix"

    # 170 - len("job name prefix") - len("-99")
    assert utils.max_order_name_length() == 151


@pytest.mark.django_db
def test_max_order_name_length_without_a_prefix_reserves_only_the_resend_suffix(smartling_settings):
    smartling_settings.JOB_NAME_PREFIX = None

    # 170 - len("-99")
    assert utils.max_order_name_length() == 167


@pytest.mark.django_db
def test_max_order_name_length_is_not_positive_when_the_prefix_fills_the_job_name(smartling_settings):
    smartling_settings.JOB_NAME_PREFIX = "x" * 170

    # 170 - len("x" * 170") - len("-99")
    assert utils.max_order_name_length() <= 0
