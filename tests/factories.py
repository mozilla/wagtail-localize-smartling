from typing import Any

import factory
import factory.django

from wagtail.models import Locale
from wagtail_localize.models import Translation
from wagtail_localize_smartling import models as wls_models

from testapp.factories import TranslationSourceFactory, UserFactory


class ProjectFactory(factory.django.DjangoModelFactory):
    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        model = wls_models.Project

    account_uid = factory.Faker("random_letters", length=16)
    archived = False
    project_id = factory.Faker("random_letters", length=16)
    name = factory.Sequence(lambda n: f"Project {n}")
    type_code = "APPLICATION_RESOURCES"
    source_locale_description = factory.SelfAttribute("source_locale_id")
    source_locale_id = "en-US"

    @factory.post_generation
    def target_locales(obj: wls_models.Project, create: bool, extracted: Any, **kwargs):  # pyright: ignore[reportGeneralTypeIssues]
        if create and not extracted:
            ProjectTargetLocaleFactory(project=obj, locale_id="de-DE")
            ProjectTargetLocaleFactory(project=obj, locale_id="fr-FR")


class ProjectTargetLocaleFactory(factory.django.DjangoModelFactory):
    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        model = wls_models.ProjectTargetLocale

    project = factory.SubFactory(ProjectFactory)
    description = factory.SelfAttribute("locale_id")
    enabled = True


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

    project = factory.SubFactory(ProjectFactory)

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
