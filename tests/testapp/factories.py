from typing import Optional

import factory
import factory.django
import wagtail_factories

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from wagtail import blocks
from wagtail.models import Site
from wagtail.rich_text import RichText
from wagtail_factories.blocks import BlockFactory

from .models import BodyBlock, InfoPage


class SuperUserFactory(factory.django.DjangoModelFactory):
    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        model = User

    username = factory.Sequence(lambda n: f"user-{n}")
    password = factory.Transformer("password", transform=lambda p: make_password(p))
    is_superuser = True


class RichTextBlockFactory(BlockFactory):
    @classmethod
    def _construct_block(cls, block_class, *args, **kwargs):
        if value := kwargs.get("value"):
            if not isinstance(value, RichText):
                value = RichText(value)
            return block_class().clean(value)
        return block_class().get_default()

    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        model = blocks.RichTextBlock


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
        extracted: Optional[Site],
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
