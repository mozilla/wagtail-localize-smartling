import pytest

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from wagtail.models import Locale
from wagtail_localize.models import Translation, TranslationSource

from wagtail_localize_smartling import models as wls_models
from wagtail_localize_smartling.api.types import JobStatus
from wagtail_localize_smartling.models import OrderState
from wagtail_localize_smartling.page_locale_table import (
    CellState,
    CoverageTable,
    build_coverage_table,
)

from testapp.factories import InfoPageFactory
from tests.factories import JobFactory, OrderFactory


def target_locales():
    return list(Locale.objects.exclude(pk=Locale.get_default().pk))


def _ordered_translations(source):
    return [Translation.objects.get_or_create(source=source, target_locale=locale)[0] for locale in target_locales()]


def _draft_order_with_one_page(page, user) -> wls_models.Order:
    source, _ = TranslationSource.get_or_create_from_instance(page)
    order = OrderFactory(submitted_at=None, created_by=user)
    order_item = wls_models.OrderItem.objects.create(order=order, translation_source=source, added_by=user)
    order_item.translations.set(_ordered_translations(source))
    return order


def _set_job_state(job, *, status, blocked=False):
    """Put a job in the state, sync dates included."""
    synced = status != JobStatus.UNSYNCED
    job.status = status
    job.sync_blocked = blocked
    job.translation_job_uid = f"smartling-job-{job.pk}" if synced else ""
    job.first_synced_at = job.last_synced_at = timezone.now() if synced else None
    job.save(update_fields=["status", "sync_blocked", "translation_job_uid", "first_synced_at", "last_synced_at"])
    return job


def _submitted_order_with_one_page(page, user):
    order = _draft_order_with_one_page(page, user)
    job = _set_job_state(order.submit(user=user), status=JobStatus.AWAITING_AUTHORIZATION)
    order.refresh_from_db()
    return order, job, order.order_items.get().translation_source


def _submitted_order_with_pages(page, user, *, page_count: int) -> wls_models.Order:
    order = OrderFactory(submitted_at=None, created_by=user)
    for index in range(page_count):
        child = InfoPageFactory(parent=page, title=f"{order.name} page {index}")
        source, _ = TranslationSource.get_or_create_from_instance(child)
        order_item = wls_models.OrderItem.objects.create(order=order, translation_source=source, added_by=user)
        order_item.translations.set(_ordered_translations(source))

    order.submit(user=user)
    order.refresh_from_db()
    return order


def _get_annotated_state(order) -> str:
    return wls_models.Order.annotate_state(wls_models.Order.objects.filter(pk=order.pk)).get().annotated_state


def _read_as_a_template_would(table: CoverageTable) -> None:
    """
    Read every value a rendered screen reads.

    The purpose of this helper function is to assert db queries for reading all
    values on the page, so we want to avoid lazy calls, and instead to read all
    values.
    """
    assert table.rows
    assert table.states_present
    for row in table.rows:
        assert row.title
        assert row.edit_url
        str(row.path)
        str(row.order_item.added_by)
        str(row.order_item.added_at)
        for cell in row.ordered_cells:
            assert cell.accessible_name


def _get_orders_in_every_state(page, user) -> list:
    """One Order per case."""
    cases = [(OrderState.NOT_SENT, OrderFactory(submitted_at=None, created_by=user))]

    for state, status, blocked, sent_pairs, landed_pairs in [
        (OrderState.FAILED, JobStatus.UNSYNCED, True, 2, 0),
        (OrderState.AWAITING_AUTHORIZATION, JobStatus.AWAITING_AUTHORIZATION, False, 2, 0),
        (OrderState.IN_TRANSLATION, JobStatus.IN_PROGRESS, False, 2, 0),
        (OrderState.IN_TRANSLATION, JobStatus.IN_PROGRESS, False, 0, 0),
        (OrderState.PARTIALLY_LANDED, JobStatus.IN_PROGRESS, False, 2, 1),
        (OrderState.COMPLETE, JobStatus.COMPLETED, False, 2, 2),
        (OrderState.CANCELLED, JobStatus.CANCELLED, False, 2, 0),
    ]:
        order = OrderFactory(created_by=user)
        child = InfoPageFactory(parent=page, title=f"{order.name} page")
        job = JobFactory(
            source_instance=child,
            order=order,
            synced=status != JobStatus.UNSYNCED,
            unsynced=status == JobStatus.UNSYNCED,
            status=status,
            sync_blocked=blocked,
            # None asks the factory for every locale; a job with nothing to
            # deliver is the one case that cannot be built that way.
            translations=None if sent_pairs else [],
        )
        for job_translation in job.job_translations.all()[:landed_pairs]:
            job_translation.imported_at = timezone.now()
            job_translation.save(update_fields=["imported_at"])
        cases.append((state, order))

    return cases


@pytest.mark.django_db
def test_a_draft_order_shows_what_will_be_sent(root_page, superuser):
    order = OrderFactory(submitted_at=None, created_by=superuser)
    source, _ = TranslationSource.get_or_create_from_instance(root_page)
    item = wls_models.OrderItem.objects.create(order=order, translation_source=source, added_by=superuser)
    french, german = target_locales()[:2]
    # German exists as a translation of this page but was not ordered.
    Translation.objects.create(source=source, target_locale=german)
    item.translations.set([Translation.objects.create(source=source, target_locale=french)])

    table = build_coverage_table(order)

    # The columns are every target locale, so the one nobody ordered still has
    # a column — that hole is what the table is meant to show.
    assert set(table.locales) == set(target_locales())
    assert table.rows[0].source_instance == root_page
    assert table.rows[0].cells[french.pk] == CellState.WILL_BE_SENT
    assert table.rows[0].cells[german.pk] == CellState.NOT_REQUESTED


@pytest.mark.django_db
def test_a_landed_locale_and_a_pending_one(smartling_project, root_page, superuser):
    order, job, source = _submitted_order_with_one_page(root_page, superuser)
    french, german = target_locales()[:2]
    landed = job.job_translations.get(translation__target_locale=french)
    landed.imported_at = timezone.now()
    landed.save(update_fields=["imported_at"])

    table = build_coverage_table(order)

    assert table.rows[0].cells[french.pk] == CellState.LANDED
    assert table.rows[0].cells[german.pk] == CellState.WITH_SMARTLING


@pytest.mark.django_db
@pytest.mark.parametrize("status,blocked", [(JobStatus.CANCELLED, False), (JobStatus.UNSYNCED, True)])
def test_a_blocked_job_turns_pending_cells_into_alerts(smartling_project, root_page, superuser, status, blocked):
    order, job, source = _submitted_order_with_one_page(root_page, superuser)
    _set_job_state(job, status=status, blocked=blocked)

    table = build_coverage_table(order)

    assert set(table.rows[0].cells.values()) == {CellState.NOT_TRANSLATED}


@pytest.mark.django_db
def test_a_locale_that_landed_before_a_resend_still_reads_as_landed(smartling_project, root_page, superuser):
    order, first_job, source = _submitted_order_with_one_page(root_page, superuser)
    french, german = target_locales()[:2]
    landed = first_job.job_translations.get(translation__target_locale=french)
    landed.imported_at = timezone.now()
    landed.save(update_fields=["imported_at"])
    _set_job_state(first_job, status=JobStatus.CANCELLED)

    order.submit(
        user=superuser,
        job_name="Resend",
        translations=[Translation.objects.get(source=source, target_locale=german)],
    )

    table = build_coverage_table(order)

    assert table.rows[0].cells[french.pk] == CellState.LANDED
    assert table.rows[0].cells[german.pk] == CellState.WITH_SMARTLING


@pytest.mark.django_db
def test_a_job_blocked_after_reaching_smartling_is_still_waiting(smartling_project, root_page, superuser):
    order, job, source = _submitted_order_with_one_page(root_page, superuser)
    job.sync_blocked = True
    job.save(update_fields=["sync_blocked"])

    table = build_coverage_table(order)

    assert set(table.rows[0].cells.values()) == {CellState.WITH_SMARTLING}
    assert order.state == OrderState.AWAITING_AUTHORIZATION
    assert _get_annotated_state(order) == OrderState.AWAITING_AUTHORIZATION.value


@pytest.mark.django_db
def test_an_order_that_landed_everywhere_is_complete_whatever_became_of_its_job(
    smartling_project, root_page, superuser
):
    """Deleting a finished job in Smartling does not take the translations back."""
    order, job, source = _submitted_order_with_one_page(root_page, superuser)
    job.job_translations.update(imported_at=timezone.now())
    _set_job_state(job, status=JobStatus.DELETED)

    assert order.state == OrderState.COMPLETE
    assert _get_annotated_state(order) == OrderState.COMPLETE.value


@pytest.mark.django_db
def test_the_state_comes_from_the_resend_not_the_blocked_send_before_it(smartling_project, root_page, superuser):
    order, first_job, source = _submitted_order_with_one_page(root_page, superuser)
    _set_job_state(first_job, status=JobStatus.UNSYNCED, blocked=True)
    _set_job_state(order.submit(user=superuser, job_name="Resend"), status=JobStatus.IN_PROGRESS)

    assert order.state == OrderState.IN_TRANSLATION
    assert _get_annotated_state(order) == OrderState.IN_TRANSLATION.value


@pytest.mark.django_db
def test_an_order_whose_pages_have_different_source_locales_keeps_every_column(root_page, superuser):
    """
    A column is dropped only for a locale every page is already written in.

    There is no column for translating English into English, so the source
    locale is excluded, but with a French page in the same order, excluding
    French too would take away the column where the English page's missing
    French translation should show as a hole.
    """
    french = Locale.objects.get(language_code="fr")
    order = OrderFactory(submitted_at=None, created_by=superuser)
    for page in [
        InfoPageFactory(parent=root_page, title="English source page"),
        InfoPageFactory(parent=root_page, title="French source page", locale=french),
    ]:
        source, _ = TranslationSource.get_or_create_from_instance(page)
        wls_models.OrderItem.objects.create(order=order, translation_source=source, added_by=superuser)

    table = build_coverage_table(order)

    assert french in table.locales


@pytest.mark.django_db
def test_a_page_sent_again_after_an_edit_stops_reading_as_landed(smartling_project, root_page, superuser):
    order, first_job, source = _submitted_order_with_one_page(root_page, superuser)
    french = target_locales()[0]
    first_job.job_translations.update(imported_at=timezone.now())
    _set_job_state(first_job, status=JobStatus.COMPLETED)

    order.submit(
        user=superuser,
        job_name="Second send",
        translations=[Translation.objects.get(source=source, target_locale=french)],
    )

    table = build_coverage_table(order)

    assert table.rows[0].cells[french.pk] == CellState.WITH_SMARTLING


@pytest.mark.django_db
def test_table_db_count_the_same_whatever_its_size(smartling_project, root_page, superuser):
    small = _submitted_order_with_pages(root_page, superuser, page_count=2)
    large = _submitted_order_with_pages(root_page, superuser, page_count=8)

    with CaptureQueriesContext(connection) as small_queries:
        _read_as_a_template_would(build_coverage_table(small))
    with CaptureQueriesContext(connection) as large_queries:
        _read_as_a_template_would(build_coverage_table(large))

    assert len(small_queries) == len(large_queries)


@pytest.mark.django_db
def test_an_order_that_was_never_saved_has_not_been_sent(superuser):
    assert wls_models.Order(name="Unsaved", created_by=superuser).state == OrderState.NOT_SENT


@pytest.mark.django_db
def test_every_kind_of_order_reads_the_state_it_is_in(root_page, superuser):
    cases = _get_orders_in_every_state(root_page, superuser)
    annotated = {order.pk: order for order in wls_models.Order.annotate_state(wls_models.Order.objects.all())}

    assert [annotated[order.pk].annotated_state for _, order in cases] == [state.value for state, _ in cases]
    assert [order.state for _, order in cases] == [state for state, _ in cases]
