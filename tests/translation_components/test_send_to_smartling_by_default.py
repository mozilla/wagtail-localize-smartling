"""
Tests for SmartlingSettings.SEND_TO_SMARTLING_BY_DEFAULT — the toggle
that controls whether the "Send to Smartling" checkbox is pre-ticked
on the submit-translations form.
"""

import pytest

from django.urls import reverse

from wagtail_localize_smartling.models import Job

from testapp.factories import InfoPageFactory


pytestmark = pytest.mark.django_db


def _get_enabled_field(client, page):
    submit_url = reverse(
        "wagtail_localize:submit_page_translation",
        kwargs={"page_id": page.pk},
    )
    response = client.get(submit_url)
    assert response.status_code == 200

    component_mgr = response.context["components"]
    assert len(component_mgr.components) == 1
    _component, _instance, form = component_mgr.components[0]

    assert "enabled" in form.fields
    return form["enabled"]


def test_send_to_smartling_default_on(
    client, root_page, superuser, smartling_project, smartling_settings
):
    smartling_settings.SEND_TO_SMARTLING_BY_DEFAULT = True

    page = InfoPageFactory(parent=root_page, title="Default-on test page")
    client.force_login(superuser)

    enabled = _get_enabled_field(client, page)
    assert enabled.field.initial is True
    assert enabled.value() is True


def test_send_to_smartling_default_off(
    client, root_page, superuser, smartling_project, smartling_settings
):
    smartling_settings.SEND_TO_SMARTLING_BY_DEFAULT = False

    page = InfoPageFactory(parent=root_page, title="Default-off test page")
    client.force_login(superuser)

    enabled = _get_enabled_field(client, page)
    assert enabled.field.initial is False
    assert enabled.value() is False


def test_settings_default_uses_django_DEV_flag(settings, smartling_settings):
    # The default value (when SEND_TO_SMARTLING_BY_DEFAULT is omitted
    # from WAGTAIL_LOCALIZE_SMARTLING) flips based on `settings.DEV`:
    # off in dev environments, on otherwise.
    from wagtail_localize_smartling.settings import _init_settings

    settings.WAGTAIL_LOCALIZE_SMARTLING = {
        "PROJECT_ID": "x",
        "USER_IDENTIFIER": "x",
        "USER_SECRET": "x",
    }

    settings.DEV = True
    resolved = _init_settings()
    assert resolved.SEND_TO_SMARTLING_BY_DEFAULT is False

    settings.DEV = False
    resolved = _init_settings()
    assert resolved.SEND_TO_SMARTLING_BY_DEFAULT is True

    del settings.DEV
    resolved = _init_settings()
    # Absent DEV setting is treated as production-like → default on.
    assert resolved.SEND_TO_SMARTLING_BY_DEFAULT is True


def test_explicit_setting_overrides_DEV_default(settings):
    from wagtail_localize_smartling.settings import _init_settings

    settings.DEV = True
    settings.WAGTAIL_LOCALIZE_SMARTLING = {
        "PROJECT_ID": "x",
        "USER_IDENTIFIER": "x",
        "USER_SECRET": "x",
        "SEND_TO_SMARTLING_BY_DEFAULT": True,
    }

    resolved = _init_settings()
    assert resolved.SEND_TO_SMARTLING_BY_DEFAULT is True
