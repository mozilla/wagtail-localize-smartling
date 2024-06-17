import dataclasses
import logging

from typing import Literal, cast

from django.conf import settings as django_settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.functional import SimpleLazyObject


logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class SmartlingSettings:
    PROJECT_ID: str
    USER_IDENTIFIER: str
    USER_SECRET: str
    REQUIRED: bool = False
    ENVIRONMENT: Literal["production", "staging"] = "production"
    API_TIMEOUT_SECONDS: float = 5.0


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

        if (api_timeout_seconds := settings_dict["API_TIMEOUT_SECONDS"]) <= 0:
            raise ImproperlyConfigured(
                f"{setting_name}['API_TIMEOUT_SECONDS'] must be a positive number"
            )
        settings_kwargs["API_TIMEOUT_SECONDS"] = api_timeout_seconds

    return SmartlingSettings(**settings_kwargs)


settings = cast(SmartlingSettings, SimpleLazyObject(_init_settings))
