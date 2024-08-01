import dataclasses
import logging

from typing import Literal, cast

from django.conf import settings as django_settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.functional import SimpleLazyObject
from django.utils.module_loading import import_string


logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class SmartlingSettings:
    PROJECT_ID: str
    USER_IDENTIFIER: str
    USER_SECRET: str
    REQUIRED: bool = False
    ENVIRONMENT: Literal["production", "staging"] = "production"
    API_TIMEOUT_SECONDS: float = 5.0
    LOCALE_TO_SMARTLING_LOCALE: "dict[str, str]" = dataclasses.field(
        default_factory=dict
    )
    SMARTLING_LOCALE_TO_LOCALE: "dict[str, str]" = dataclasses.field(
        default_factory=dict
    )
    REFORMAT_LANGUAGE_CODES: bool = True


def _init_settings() -> SmartlingSettings:
    """
    Get and validate Smartling settings from the Django settings.
    """
    setting_name = "WAGTAIL_LOCALIZE_SMARTLING"
    settings_dict = getattr(django_settings, setting_name, {})

    # Validate required fields

    project_id = settings_dict.get("PROJECT_ID", "")
    user_identifier = settings_dict.get("USER_IDENTIFIER", "")
    user_secret = settings_dict.get("USER_SECRET", "")
    if not all((project_id, user_identifier, user_secret)):
        raise ImproperlyConfigured(
            f"{setting_name} must declare PROJECT_ID, USER_IDENTIFIER, and USER_SECRET"
        )

    settings_kwargs = {
        "PROJECT_ID": project_id,
        "USER_IDENTIFIER": user_identifier,
        "USER_SECRET": user_secret,
    }

    # Validate optional fields

    if "REQUIRED" in settings_dict:
        settings_kwargs["REQUIRED"] = bool(settings_dict["REQUIRED"])

    if "ENVIRONMENT" in settings_dict:
        if (environment := settings_dict["ENVIRONMENT"]) not in (
            "production",
            "staging",
        ):
            raise ImproperlyConfigured(
                f"{setting_name}['ENVIRONMENT'] must be 'production' or 'staging'"
            )
        settings_kwargs["ENVIRONMENT"] = environment

    if "API_TIMEOUT_SECONDS" in settings_dict:
        api_timeout_seconds_str = settings_dict["API_TIMEOUT_SECONDS"]
        try:
            api_timeout_seconds = float(api_timeout_seconds_str)
        except ValueError as e:
            raise ImproperlyConfigured(
                f"{setting_name}['API_TIMEOUT_SECONDS'] must be a number"
            ) from e

        if api_timeout_seconds <= 0:
            raise ImproperlyConfigured(
                f"{setting_name}['API_TIMEOUT_SECONDS'] must be a positive number"
            )
        settings_kwargs["API_TIMEOUT_SECONDS"] = api_timeout_seconds

    if (
        "LOCALE_MAPPING_CALLBACK" in settings_dict
        and "LOCALE_TO_SMARTLING_LOCALE" in settings_dict
    ):
        raise ImproperlyConfigured(
            f"{setting_name} cannot have both LOCALE_MAPPING_CALLBACK "
            f"and LOCALE_TO_SMARTLING_LOCALE"
        )

    if "LOCALE_MAPPING_CALLBACK" in settings_dict:
        func_or_path = settings_dict["LOCALE_MAPPING_CALLBACK"]
        if isinstance(func_or_path, str):
            func_or_path = import_string(func_or_path)

        LOCALE_TO_SMARTLING_LOCALE: dict[str, str] = {}
        SMARTLING_LOCALE_TO_LOCALE: dict[str, str] = {}

        for locale_id, _locale in getattr(
            django_settings, "WAGTAIL_CONTENT_LANGUAGES", []
        ):
            if mapped_locale_id := func_or_path(locale_id):
                LOCALE_TO_SMARTLING_LOCALE[locale_id] = mapped_locale_id
                SMARTLING_LOCALE_TO_LOCALE[mapped_locale_id] = locale_id

        settings_kwargs["LOCALE_TO_SMARTLING_LOCALE"] = LOCALE_TO_SMARTLING_LOCALE
        settings_kwargs["SMARTLING_LOCALE_TO_LOCALE"] = SMARTLING_LOCALE_TO_LOCALE

    elif "LOCALE_TO_SMARTLING_LOCALE" in settings_dict:
        if not isinstance(settings_dict["LOCALE_TO_SMARTLING_LOCALE"], dict):
            raise ImproperlyConfigured(
                f"{setting_name}['LOCALE_TO_SMARTLING_LOCALE'] must be a dictionary "
                f"with the Wagtail locale id as key and the Smartling locale as value"
            )
        LOCALE_TO_SMARTLING_LOCALE = settings_dict["LOCALE_TO_SMARTLING_LOCALE"].copy()
        for locale_id, _locale in getattr(
            django_settings, "WAGTAIL_CONTENT_LANGUAGES", []
        ):
            if locale_id not in LOCALE_TO_SMARTLING_LOCALE:
                LOCALE_TO_SMARTLING_LOCALE[locale_id] = locale_id

        settings_kwargs["LOCALE_TO_SMARTLING_LOCALE"] = LOCALE_TO_SMARTLING_LOCALE
        settings_kwargs["SMARTLING_LOCALE_TO_LOCALE"] = {
            v: k for k, v in LOCALE_TO_SMARTLING_LOCALE.items()
        }

    if "REFORMAT_LANGUAGE_CODES" in settings_dict:
        settings_kwargs["REFORMAT_LANGUAGE_CODES"] = bool(
            settings_dict["REFORMAT_LANGUAGE_CODES"]
        )

    return SmartlingSettings(**settings_kwargs)


settings = cast(SmartlingSettings, SimpleLazyObject(_init_settings))
