import dataclasses
import logging

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Literal, cast

from django.conf import settings as django_settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.functional import SimpleLazyObject
from django.utils.module_loading import import_string


if TYPE_CHECKING:
    from wagtail_localize.models import Translation, TranslationSource

    from wagtail_localize_smartling.models import Job

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
    JOB_NAME_PREFIX: str | None = None
    JOB_DESCRIPTION_CALLBACK: (
        Callable[[str, "TranslationSource", Iterable["Translation"]], str] | None
    ) = None
    VISUAL_CONTEXT_CALLBACK: Callable[["Job"], tuple[str, str]] | None = None
    TRANSLATION_APPROVER_GROUP_NAME: str = "Translation approver"
    ADD_APPROVAL_TASK_TO_DASHBOARD: bool = True
    MAX_APPROVAL_TASKS_ON_DASHBOARD: int = 7
    SEND_EMAIL_ON_TRANSLATION_IMPORT: bool = True


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

    if "JOB_DESCRIPTION_CALLBACK" in settings_dict:
        func_or_path = settings_dict["JOB_DESCRIPTION_CALLBACK"]
        if isinstance(func_or_path, str):
            func_or_path = import_string(func_or_path)

        if callable(func_or_path):
            settings_kwargs["JOB_DESCRIPTION_CALLBACK"] = func_or_path

    if "VISUAL_CONTEXT_CALLBACK" in settings_dict:
        func_or_path = settings_dict["VISUAL_CONTEXT_CALLBACK"]
        if isinstance(func_or_path, str):
            func_or_path = import_string(func_or_path)

        if callable(func_or_path):
            settings_kwargs["VISUAL_CONTEXT_CALLBACK"] = func_or_path

    if "JOB_NAME_PREFIX" in settings_dict:
        settings_kwargs["JOB_NAME_PREFIX"] = settings_dict["JOB_NAME_PREFIX"]

    if "ADD_APPROVAL_TASK_TO_DASHBOARD" in settings_dict:
        settings_kwargs["ADD_APPROVAL_TASK_TO_DASHBOARD"] = settings_dict[
            "ADD_APPROVAL_TASK_TO_DASHBOARD"
        ]

    if "TRANSLATION_APPROVER_GROUP_NAME" in settings_dict:
        settings_kwargs["TRANSLATION_APPROVER_GROUP_NAME"] = settings_dict[
            "TRANSLATION_APPROVER_GROUP_NAME"
        ]

    if "ADD_APPROVAL_TASK_TO_DASHBOARD" in settings_dict:
        settings_kwargs["ADD_APPROVAL_TASK_TO_DASHBOARD"] = settings_dict[
            "ADD_APPROVAL_TASK_TO_DASHBOARD"
        ]

    if "MAX_APPROVAL_TASKS_ON_DASHBOARD" in settings_dict:
        settings_kwargs["MAX_APPROVAL_TASKS_ON_DASHBOARD"] = settings_dict[
            "MAX_APPROVAL_TASKS_ON_DASHBOARD"
        ]

    if "SEND_EMAIL_ON_TRANSLATION_IMPORT" in settings_dict:
        settings_kwargs["SEND_EMAIL_ON_TRANSLATION_IMPORT"] = settings_dict[
            "SEND_EMAIL_ON_TRANSLATION_IMPORT"
        ]

    return SmartlingSettings(**settings_kwargs)


settings = cast(SmartlingSettings, SimpleLazyObject(_init_settings))
