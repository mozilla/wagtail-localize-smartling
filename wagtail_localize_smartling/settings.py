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
    ENVIRONMENT: Literal["production", "staging"]
    API_TIMEOUT_SECONDS: float


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

    # Validate ENVIRONMENT
    environment = settings_dict.get("ENVIRONMENT", "production")
    if environment not in {"production", "staging"}:
        raise ImproperlyConfigured(
            f'{setting_name}["ENVIRONMENT"] must be either "staging" or "production"'
        )

    # Validate API_TIMEOUT_SECONDS
    api_timeout_seconds = settings_dict.get("API_TIMEOUT_SECONDS", 5)
    if not isinstance(api_timeout_seconds, (int, float)) or api_timeout_seconds <= 0:
        raise ImproperlyConfigured(
            f'{setting_name}["API_TIMEOUT_SECONDS"] must be a positive number'
        )

    return SmartlingSettings(
        PROJECT_ID=project_id,
        USER_IDENTIFIER=user_identifier,
        USER_SECRET=user_secret,
        ENVIRONMENT=environment,
        API_TIMEOUT_SECONDS=api_timeout_seconds,
    )


settings = cast(SmartlingSettings, SimpleLazyObject(_init_settings))
