import factory
import factory.django
import wagtail_factories

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone
from wagtail import blocks
from wagtail.models import Locale, Site, TranslatableMixin
from wagtail_localize.models import TranslatableObject, Translation, TranslationSource

from .models import BodyBlock, InfoPage, InfoSnippet


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        model = User

    username = factory.Sequence(lambda n: f"user-{n}")
    password = factory.Transformer("password", transform=lambda p: make_password(p))


# https://github.com/nhsuk/wagtail-factories/blob/9f1d2221773282ac41e1c431edef01abc011a507/src/wagtail_factories/blocks.py#L161
class RichTextBlockFactory(factory.Factory):
    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        model = blocks.RichTextBlock

    @classmethod
    def _build(cls, model_class, *args, value="", **kwargs):
        block = model_class()
        return block.to_python(value)

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        return cls._build(model_class, *args, **kwargs)


class BodyBlockFactory(wagtail_factories.StreamBlockFactory):
    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        model = BodyBlock

    heading = factory.SubFactory(wagtail_factories.CharBlockFactory)
    paragraph = factory.SubFactory(RichTextBlockFactory)
    document = factory.SubFactory(wagtail_factories.DocumentChooserBlockFactory)
    image = factory.SubFactory(wagtail_factories.ImageChooserBlockFactory)


class InfoPageFactory(wagtail_factories.PageFactory):
    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        model = InfoPage

    body = wagtail_factories.StreamFieldFactory(BodyBlockFactory)

    @factory.post_generation
    def site(
        obj: InfoPage,  # pyright: ignore[reportGeneralTypeIssues]
        create: bool,
        extracted: Site | None,
        **kwargs,
    ):
        """
        Post-generation hook for making sure a default site exists when creating
        InfoPage instances with this factory.

        If a Site object is passed in, its root page will be set to the
        just-created InfoPage.

        Otherwise, if no default Site exists and the just-created InfoPage is at
        the top level of the page tree, it will be set as the root page of a new
        default Site.  Any kwargs passed in will be passed to the SiteFactory
        used to create the Site.
        """
        if (
            not create
            or obj.depth != 2
            or Site.objects.filter(is_default_site=True).exists()
        ):
            return

        if extracted is not None:
            extracted.root_page = obj
            extracted.save()
            return extracted

        return wagtail_factories.SiteFactory(
            **{
                "hostname": "testserver",
                "port": 80,
                "site_name": "wagtail-localize-smartling test site",
                "root_page": obj,
                "is_default_site": True,
                **kwargs,
            }
        )


class InfoSnippetFactory(factory.django.DjangoModelFactory):
    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        model = InfoSnippet


class TranslatablebObjectFactory(factory.django.DjangoModelFactory):
    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        model = TranslatableObject


class LocaleFactory(factory.django.DjangoModelFactory):
    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        model = Locale


def get_translatableobject_content_type(obj: TranslatableMixin) -> ContentType:
    for cls in type(obj).mro():
        if issubclass(cls, models.Model):
            for field in cls._meta.get_fields(include_parents=False):
                if field.name == "translation_key":
                    return ContentType.objects.get_for_model(cls)
    raise ValueError("obj must be a TranslatableMixin instance")


class TranslationSourceFactory(factory.django.DjangoModelFactory):
    """
    When calling this factory, you must pass in a source_instance kwarg that is
    an instance of TranslatableMixin. The translation key, locale and content
    types will be derived from this.
    """

    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        model = TranslationSource
        exclude = (
            "source_instance",
            "translatableobject_content_type",
        )

    object = factory.SubFactory(
        TranslatablebObjectFactory,
        translation_key=factory.SelfAttribute("..source_instance.translation_key"),
        content_type=factory.SelfAttribute("..translatableobject_content_type"),
    )
    specific_content_type = factory.LazyAttribute(
        lambda obj: ContentType.objects.get_for_model(obj.source_instance)
    )
    translatableobject_content_type = factory.LazyAttribute(
        lambda obj: get_translatableobject_content_type(obj.source_instance)
    )
    locale = factory.SelfAttribute("source_instance.locale")
    last_updated_at = factory.LazyFunction(timezone.now)


class TranslationFactory(factory.django.DjangoModelFactory):
    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        model = Translation

    uuid = factory.Faker("uuid4")
    source = factory.SubFactory(TranslationSourceFactory)
    target_locale = factory.SubFactory(LocaleFactory)
