"""
The page-by-locale table shown on both order screens.

Every state is derived from rows already in the database, so the screen makes
no Smartling request and has nothing that can go stale.
"""

import dataclasses
import enum

from typing import TYPE_CHECKING, Any

from django.utils.translation import gettext_lazy as _
from wagtail.models import Locale

from .settings import settings as smartling_settings


if TYPE_CHECKING:
    from .models import Order, OrderItem


class CellState(enum.Enum):
    WILL_BE_SENT = "will-be-sent"
    LANDED = "landed"
    WITH_SMARTLING = "with-smartling"
    NOT_TRANSLATED = "not-translated"
    NOT_REQUESTED = "not-requested"

    @property
    def label(self) -> str:
        return {
            CellState.WILL_BE_SENT: _("will be sent"),
            CellState.LANDED: _("landed in Wagtail"),
            CellState.WITH_SMARTLING: _("with Smartling"),
            CellState.NOT_TRANSLATED: _("not translated"),
            CellState.NOT_REQUESTED: _("not requested"),
        }[self]

    @property
    def icon_name(self) -> str:
        return {
            CellState.WILL_BE_SENT: "arrow-right",
            CellState.LANDED: "check",
            CellState.WITH_SMARTLING: "time",
            CellState.NOT_TRANSLATED: "warning",
            CellState.NOT_REQUESTED: "minus",
        }[self]


@dataclasses.dataclass
class Cell:
    locale: Locale
    state: CellState

    @property
    def accessible_name(self) -> str:
        return f"{self.locale.get_display_name()}: {self.state.label}"


@dataclasses.dataclass
class PageRow:
    order_item: "OrderItem"
    source_instance: Any
    # Keyed by locale pk for lookups and assertions; ordered_cells is the same
    # data in column order, which is what a template can iterate.
    cells: dict[int, CellState]
    ordered_cells: list[Cell]


@dataclasses.dataclass
class CoverageTable:
    locales: list[Locale]
    rows: list[PageRow]
    landed_count: int
    page_locale_pair_count: int


def build_coverage_table(order: "Order") -> CoverageTable:
    latest_job = order.get_latest_job()
    items = order.order_items.select_related(
        "translation_source",
        "translation_source__specific_content_type",
        "translation_source__locale",
    )
    if latest_job is None:
        # Only a draft reads these, and a large order holds one row per page
        # and locale.
        items = items.prefetch_related("translations")

    items = list(items)
    locales = _get_column_locales(items)
    source_instances = _get_source_instances(items)
    page_locale_states = (
        _get_ordered_page_locale_states(items) if latest_job is None else _get_sent_page_locale_states(order)
    )

    rows = []
    for item in items:
        cells = {
            locale.pk: page_locale_states.get((item.translation_source_id, locale.pk), CellState.NOT_REQUESTED)
            for locale in locales
        }
        rows.append(
            PageRow(
                order_item=item,
                source_instance=source_instances.get(_instance_key(item.translation_source)),
                cells=cells,
                ordered_cells=[Cell(locale=locale, state=cells[locale.pk]) for locale in locales],
            )
        )

    states = [state for row in rows for state in row.cells.values()]
    return CoverageTable(
        locales=locales,
        rows=rows,
        landed_count=states.count(CellState.LANDED),
        page_locale_pair_count=sum(1 for state in states if state is not CellState.NOT_REQUESTED),
    )


def _get_column_locales(items: "list[OrderItem]") -> list[Locale]:
    """
    Every locale the project translates into, not only the ordered ones.

    A locale nobody ticked has to keep its column: the empty cells under it are
    the only warning that a page was left out of it.
    """
    source_locale_ids = {item.translation_source.locale_id for item in items} or {Locale.get_default().pk}
    # Only a locale every page is written in loses its column. Where the pages
    # disagree, each row needs the others' columns to show its own gaps.
    queryset = Locale.objects.exclude(pk__in=source_locale_ids if len(source_locale_ids) == 1 else [])
    if smartling_settings.EXCLUDE_LOCALES:
        queryset = queryset.exclude(language_code__in=smartling_settings.EXCLUDE_LOCALES)
    return list(queryset)


def _instance_key(translation_source) -> tuple[int, int, str]:
    return (
        translation_source.specific_content_type_id,
        translation_source.locale_id,
        str(translation_source.object_id),
    )


def _get_source_instances(items: "list[OrderItem]") -> dict[tuple[int, int, str], Any]:
    """
    Resolve every row's page in bulk, one query per content type.

    `TranslationSource.get_source_instance()` costs a query each time, and the
    template asks every row for its title and its edit link.
    """
    groups: dict[tuple[int, int], Any] = {}
    for item in items:
        source = item.translation_source
        group = groups.setdefault(
            (source.specific_content_type_id, source.locale_id),
            {"model": source.specific_content_type.model_class(), "object_ids": []},
        )
        group["object_ids"].append(source.object_id)

    instances = {}
    for (content_type_id, locale_id), group in groups.items():
        model = group["model"]
        if model is None:
            continue
        for instance in model.objects.filter(translation_key__in=group["object_ids"], locale_id=locale_id):
            instances[(content_type_id, locale_id, str(instance.translation_key))] = instance
    return instances


def _get_ordered_page_locale_states(items: "list[OrderItem]") -> dict[tuple[int, int], CellState]:
    """What a draft order will send: the locales the editor ticked, nothing else."""
    return {
        (item.translation_source_id, translation.target_locale_id): CellState.WILL_BE_SENT
        for item in items
        for translation in item.translations.all()
    }


def _get_sent_page_locale_states(order: "Order") -> dict[tuple[int, int], CellState]:
    """What every send of this order did with each page and locale."""
    # Imported here because models.py imports this module for the order state.
    from .models import JobTranslation

    states: dict[tuple[int, int], CellState] = {}
    job_translations = (
        JobTranslation.objects.filter(job__order=order).select_related("job", "translation").order_by("job_id")
    )
    for job_translation in job_translations:
        translation = job_translation.translation
        key = (translation.source_id, translation.target_locale_id)
        if job_translation.imported_at is not None:
            states[key] = CellState.LANDED
        else:
            # A clock on a job nothing will revive tells the editor to keep
            # waiting for something that will never arrive — but a job blocked
            # after its files reached Smartling can still deliver these locales.
            job_is_dead = job_translation.job.needs_a_new_job
            states[key] = CellState.NOT_TRANSLATED if job_is_dead else CellState.WITH_SMARTLING

    return states
