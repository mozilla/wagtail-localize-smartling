from typing import Any

import factory
import factory.django

from wagtail.models import Locale
from wagtail_localize.models import Translation

from wagtail_localize_smartling import models as wls_models
from wagtail_localize_smartling.settings import settings as smartling_settings

from testapp.factories import TranslationSourceFactory, UserFactory


class JobFactory(factory.django.DjangoModelFactory):
    """
    When calling this factory, you must pass in a source_instance kwarg that is
    an instance of TranslatableMixin. This will be passed on to
    TranslationSourceFactory.
    """

    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        model = wls_models.Job
        exclude = ("source_instance",)

    class Params:
        unsynced = factory.Trait(translation_job_uid="")

    project = factory.LazyFunction(wls_models.Project.get_current)

    user = factory.SubFactory(UserFactory)
    translation_source = factory.SubFactory(
        TranslationSourceFactory,
        source_instance=factory.SelfAttribute("..source_instance"),
    )

    name = factory.Sequence(lambda n: f"Job {n}")
    description = factory.SelfAttribute("name")
    due_date = None

    @factory.post_generation
    def translations(obj: wls_models.Job, create: bool, extracted: Any, **kwargs):  # pyright: ignore[reportGeneralTypeIssues]
        if not create:
            return

        if extracted:
            obj.translations.set(extracted)
            return

        target_locales = Locale.objects.exclude(pk=obj.translation_source.locale.pk)
        for target_locale in target_locales:
            Translation.objects.create(
                source=obj.translation_source,
                target_locale=target_locale,
            )


class TranslationApproverGroupFactory(factory.django.DjangoModelFactory):
    class Meta:  # type: ignore
        model = "auth.Group"
        django_get_or_create = ("name",)

    name = smartling_settings.TRANSLATION_APPROVER_GROUP_NAME


class WagtailUserFactory(factory.django.DjangoModelFactory):
    class Meta:  # type: ignore
        model = "auth.User"  # Equivalent to ``model = myapp.models.User``
        django_get_or_create = ("username",)

    email = "testuser@example.com"
    username = "testuser"

    is_superuser = False
    is_staff = True
    is_active = True
