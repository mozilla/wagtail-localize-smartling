"""
Tests for SmartlingSettings.SEND_TO_SMARTLING_BY_DEFAULT — the toggle
that controls whether the "Send to Smartling" checkbox is pre-ticked
on the submit/update-translation forms.

Pre-ticking is implemented client-side via an `insert_global_admin_js`
hook (see `wagtail_hooks.default_send_to_smartling_js`) that emits a
`<script src>` tag pointing to a static asset. These tests assert on
the rendered admin response containing (or omitting) that tag.
"""

import pytest

from django.urls import reverse


pytestmark = pytest.mark.django_db


SCRIPT_PATH = "wagtail_localize_smartling/js/default-send-to-smartling.js"


def test_js_hook_renders_script_when_default_on(client, superuser, smartling_settings):
    smartling_settings.SEND_TO_SMARTLING_BY_DEFAULT = True
    client.force_login(superuser)

    response = client.get(reverse("wagtailadmin_home"))
    body = response.content.decode()

    assert SCRIPT_PATH in body


def test_js_hook_omits_script_when_default_off(client, superuser, smartling_settings):
    smartling_settings.SEND_TO_SMARTLING_BY_DEFAULT = False
    client.force_login(superuser)

    response = client.get(reverse("wagtailadmin_home"))
    body = response.content.decode()

    assert SCRIPT_PATH not in body


def test_settings_default_uses_django_DEV_flag(settings):
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
