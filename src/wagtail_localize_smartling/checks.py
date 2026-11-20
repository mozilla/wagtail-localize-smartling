from typing import cast

from django.core.checks import Error, register
from django.core.checks import Warning as CheckWarning
from django.core.exceptions import ImproperlyConfigured

from .models import Order
from .settings import settings as smartling_settings
from .utils import LONGEST_RESEND_SUFFIX_LENGTH, max_job_name_length, max_order_name_length


@register()
def smartling_settings_check(app_configs, **kwargs):
    errors = []
    try:
        str(smartling_settings.PROJECT_ID)
    except ImproperlyConfigured as e:
        errors.append(
            Error(
                e.args[0],
                id="wagtail_localize_smartling.E001",
            )
        )
        # Reading any other setting would raise the same way, so the checks
        # below wait until this one is answered.
        return errors

    errors.extend(_job_name_prefix_messages())
    return errors


def _job_name_prefix_messages():
    usable_length = max_order_name_length()
    order_name_length = cast(int, Order._meta.get_field("name").max_length)  # pyright: ignore[reportAttributeAccessIssue]
    if usable_length >= order_name_length:
        return []

    job_name_length = max_job_name_length()
    prefix_length = len(smartling_settings.JOB_NAME_PREFIX or "")

    longest_usable_prefix_length = job_name_length - order_name_length - LONGEST_RESEND_SUFFIX_LENGTH - 1
    hint = (
        f"Shorten WAGTAIL_LOCALIZE_SMARTLING['JOB_NAME_PREFIX'] to {longest_usable_prefix_length} characters or fewer."
    )

    if usable_length <= 0:
        return [
            Error(
                f"The Smartling job name prefix is {prefix_length} characters, which leaves no room "
                f"for an order's name in the {job_name_length} characters Smartling allows a job name. "
                f"No order can be sent.",
                hint=hint,
                id="wagtail_localize_smartling.E002",
            )
        ]

    return [
        CheckWarning(
            f"The Smartling job name prefix is {prefix_length} characters, which leaves room for "
            f"{usable_length} characters of an order's name rather than the {order_name_length} "
            f"the field allows. Longer names will be refused.",
            hint=hint,
            id="wagtail_localize_smartling.W001",
        )
    ]
