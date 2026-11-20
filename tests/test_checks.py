import pytest

from wagtail_localize_smartling.checks import smartling_settings_check


pytestmark = pytest.mark.django_db


def test_no_messages_without_a_job_name_prefix(smartling_settings):
    smartling_settings.JOB_NAME_PREFIX = None

    assert smartling_settings_check(app_configs=None) == []


def test_no_messages_for_a_prefix_that_leaves_a_whole_order_name(smartling_settings):
    smartling_settings.JOB_NAME_PREFIX = "job name prefix"

    assert smartling_settings_check(app_configs=None) == []


def test_a_prefix_that_crowds_out_part_of_an_order_name_warns(smartling_settings):
    smartling_settings.JOB_NAME_PREFIX = "x" * 120

    messages = smartling_settings_check(app_configs=None)

    assert [message.id for message in messages] == ["wagtail_localize_smartling.W001"]
    assert "46 characters of an order's name" in messages[0].msg
    assert messages[0].hint == "Shorten WAGTAIL_LOCALIZE_SMARTLING['JOB_NAME_PREFIX'] to 66 characters or fewer."


def test_a_prefix_that_fills_a_job_name_is_an_error(smartling_settings):
    smartling_settings.JOB_NAME_PREFIX = "x" * 170

    messages = smartling_settings_check(app_configs=None)

    assert [message.id for message in messages] == ["wagtail_localize_smartling.E002"]


def test_the_longest_prefix_the_hint_offers_warns_about_nothing(smartling_settings):
    smartling_settings.JOB_NAME_PREFIX = "x" * 66

    assert smartling_settings_check(app_configs=None) == []


def test_one_character_more_than_the_hint_offers_warns(smartling_settings):
    smartling_settings.JOB_NAME_PREFIX = "x" * 67

    messages = smartling_settings_check(app_configs=None)

    assert [message.id for message in messages] == ["wagtail_localize_smartling.W001"]


def test_a_prefix_leaving_room_for_one_character_warns_rather_than_errors(smartling_settings):
    smartling_settings.JOB_NAME_PREFIX = "x" * 165

    messages = smartling_settings_check(app_configs=None)

    assert [message.id for message in messages] == ["wagtail_localize_smartling.W001"]


def test_a_prefix_leaving_room_for_nothing_errors(smartling_settings):
    smartling_settings.JOB_NAME_PREFIX = "x" * 166

    messages = smartling_settings_check(app_configs=None)

    assert [message.id for message in messages] == ["wagtail_localize_smartling.E002"]
