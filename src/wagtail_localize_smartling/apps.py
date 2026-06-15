from django.apps import AppConfig


_PATCH_APPLIED = False


class WagtailLocalizeSmartlingAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    label = "wagtail_localize_smartling"
    name = "wagtail_localize_smartling"
    verbose_name = "Wagtail Localize Smartling"

    def ready(self):
        from . import checks, signal_handlers, signals  # noqa: F401

        _patch_translation_component_default_enabled()


def _patch_translation_component_default_enabled():
    """
    Make the "Send to Smartling" checkbox honour
    SmartlingSettings.SEND_TO_SMARTLING_BY_DEFAULT.

    wagtail-localize's BaseComponentManager.from_request hardcodes the
    `enabled` form field's `initial` to `component_instance is not None`,
    which is always False for translation components (their
    get_component_instance returns None). We patch the method to also
    consult our setting for the smartling Job component, so the host
    project can opt the checkbox into being pre-ticked.
    """
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return

    from django import forms

    from wagtail_localize.components import (
        BaseComponentManager,
        accepts_parameter,
    )

    @classmethod
    def from_request(cls, request, source_object_instance=None):
        components = []

        for component in cls.get_components():
            component_model = component["model"]

            component_instance = cls.get_component_instance(
                component_model, source_object_instance=source_object_instance
            )
            edit_handler = cls.get_component_edit_handler(component_model)
            edit_handler = edit_handler.bind_to_model(component_model)

            form_class = edit_handler.get_form_class()

            if not component["required"]:
                initial_enabled = (
                    component_instance is not None
                    or _is_smartling_default_enabled(component)
                )
                form_class = type(
                    form_class.__name__,
                    (form_class,),
                    {
                        "enabled": forms.BooleanField(initial=initial_enabled),
                        "field_order": ["enabled"],
                    },
                )

            prefix = f"component-{component_model._meta.db_table}"

            form_kwargs = {
                "instance": component_instance,
                "prefix": prefix,
            }
            if accepts_parameter(form_class, "source_object_instance"):
                form_kwargs["source_object_instance"] = source_object_instance
            if accepts_parameter(form_class, "user"):
                form_kwargs["user"] = request.user

            if request.method == "POST":
                form = form_class(request.POST, request.FILES, **form_kwargs)
            else:
                form = form_class(**form_kwargs)

            components.append((component, component_instance, form))

        return cls(components)

    BaseComponentManager.from_request = from_request  # pyright: ignore[reportAttributeAccessIssue]
    _PATCH_APPLIED = True


def _is_smartling_default_enabled(component):
    """
    Return True iff `component` is the smartling Job entry AND the host
    project has opted into a default-on "Send to Smartling" checkbox.
    """
    from .models import Job
    from .settings import settings as smartling_settings

    if component.get("slug") != Job._meta.db_table:
        return False
    return bool(smartling_settings.SEND_TO_SMARTLING_BY_DEFAULT)
