from django.db import models
from wagtail import blocks
from wagtail.admin.panels import FieldPanel
from wagtail.documents.blocks import DocumentChooserBlock
from wagtail.fields import StreamField
from wagtail.images.blocks import ImageChooserBlock
from wagtail.models import Page, TranslatableMixin
from wagtail.snippets.models import register_snippet


class BodyBlock(blocks.StreamBlock):
    heading = blocks.CharBlock(
        form_classname="title",
        icon="title",
    )
    paragraph = blocks.RichTextBlock()
    document = DocumentChooserBlock()
    image = ImageChooserBlock()


class InfoPage(Page):
    body = StreamField(BodyBlock(), use_json_field=True)

    content_panels = Page.content_panels + [
        FieldPanel("body"),
    ]


@register_snippet
class InfoSnippet(TranslatableMixin):
    content = models.TextField()
