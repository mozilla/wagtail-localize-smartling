import pytest

from django.contrib.contenttypes.models import ContentType
from wagtail.models import Locale

from wagtail_localize_smartling.components import LandedTranslationsPanel
from wagtail_localize_smartling.models import LandedTranslationTask

from testapp.models import InfoPage


pytestmark = pytest.mark.django_db


def _make_incomplete_tasks(count):
    locale, _ = Locale.objects.get_or_create(language_code="fr")
    content_type = ContentType.objects.get_for_model(InfoPage)
    for i in range(count):
        LandedTranslationTask.objects.create(
            content_type=content_type,
            object_id=i + 1,
            relevant_locale=locale,
            completed_on=None,
            cancelled_on=None,
        )


def test_LandedTranslationsPanel_reports_total_not_sliced_count():
    # Regression test for #64: the panel heading and "See all" link must show
    # the full count of incomplete tasks, not the size of the truncated slice.
    total = 12
    max_to_show = 7
    _make_incomplete_tasks(total)

    panel = LandedTranslationsPanel(max_to_show=max_to_show)
    context = panel.get_context_data()

    assert context["total_tasks"] == total
    assert len(context["tasks"]) == max_to_show

    rendered = panel.render_html()

    assert f"{total} translations must be published" in rendered
    assert f"See all {total} tasks" in rendered
    # The truncated count must not appear as either the headline or the link.
    assert f"See all {max_to_show} tasks" not in rendered


def test_LandedTranslationsPanel_hides_see_all_when_not_truncated():
    max_to_show = 7
    _make_incomplete_tasks(3)

    panel = LandedTranslationsPanel(max_to_show=max_to_show)
    rendered = panel.render_html()

    assert "See all" not in rendered
