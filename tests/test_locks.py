from datetime import timedelta

import pytest

from django.contrib.auth.models import AnonymousUser
from django.db import connection, models
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from wagtail.locks import BasicLock, ScheduledForPublishLock
from wagtail_localize.models import Translation

from wagtail_localize_smartling.api.types import JobStatus
from wagtail_localize_smartling.locks import (
    PendingTranslationLock,
    PendingTranslationLockMixin,
    get_in_flight_locales_for_instance,
    get_pending_translation_state,
    is_in_flight_target,
)
from wagtail_localize_smartling.models import Job, JobTranslation

from testapp.factories import InfoPageFactory


pytestmark = pytest.mark.django_db


def source_page_of(job):
    return job.translation_source.get_source_instance()


def target_page_of(translation):
    """Materialise the translated page, so the predicates get a real instance."""
    translation.save_target(publish=False)
    return translation.get_target_instance()


def language_codes(locales):
    return [locale.language_code for locale in locales]


def set_status(job, status):
    """Move `job` to `status`, keeping its sync fields consistent."""
    unsynced = status == JobStatus.UNSYNCED
    now = timezone.now()
    Job.objects.filter(pk=job.pk).update(
        status=status,
        first_synced_at=None if unsynced else (job.first_synced_at or now),
        last_synced_at=None if unsynced else (job.last_synced_at or now),
        translation_job_uid=("" if unsynced else (job.translation_job_uid or f"test-uid-{job.pk}")),
    )


def test_page_with_no_jobs_is_not_in_flight(root_page, locales):
    page = InfoPageFactory(parent=root_page, title="Never translated")

    assert get_in_flight_locales_for_instance(page) == []
    assert is_in_flight_target(page) is False


def test_pending_job_puts_its_target_locale_in_flight(smartling_job):
    assert language_codes(get_in_flight_locales_for_instance(source_page_of(smartling_job))) == ["fr"]


@pytest.mark.parametrize(
    "status",
    [
        JobStatus.UNSYNCED,
        JobStatus.DRAFT,
        JobStatus.AWAITING_AUTHORIZATION,
        JobStatus.IN_PROGRESS,
    ],
)
def test_every_outstanding_status_locks(smartling_job, status):
    set_status(smartling_job, status)
    assert language_codes(get_in_flight_locales_for_instance(source_page_of(smartling_job))) == ["fr"]


def test_a_page_locks_from_the_moment_of_submission(smartling_job):
    set_status(smartling_job, JobStatus.UNSYNCED)
    assert is_in_flight_target(target_page_of(smartling_job.translations.get())) is True


@pytest.mark.parametrize(
    "status",
    [JobStatus.COMPLETED, JobStatus.CLOSED, JobStatus.CANCELLED, JobStatus.DELETED],
)
def test_finished_statuses_do_not_lock(smartling_job, status):
    set_status(smartling_job, status)

    assert get_in_flight_locales_for_instance(source_page_of(smartling_job)) == []


def test_imported_locale_is_no_longer_in_flight(smartling_job):
    JobTranslation.objects.filter(job=smartling_job).update(imported_at=timezone.now())
    assert get_in_flight_locales_for_instance(source_page_of(smartling_job)) == []


def test_disabled_translation_is_not_in_flight(smartling_job):
    Translation.objects.filter(smartling_jobs=smartling_job).update(enabled=False)
    assert get_in_flight_locales_for_instance(source_page_of(smartling_job)) == []


def test_german_unlocks_while_french_is_still_pending(smartling_job_multi_locale):
    """A job is not all-or-nothing: it lands one locale at a time."""
    job = smartling_job_multi_locale
    fr = job.translations.get(target_locale__language_code="fr")
    de = job.translations.get(target_locale__language_code="de")
    fr_page = target_page_of(fr)
    de_page = target_page_of(de)

    # The "fr" job gets importer.
    JobTranslation.objects.filter(job=job, translation=de).update(imported_at=timezone.now())

    assert language_codes(get_in_flight_locales_for_instance(source_page_of(job))) == ["fr"]
    assert is_in_flight_target(fr_page) is True
    assert is_in_flight_target(de_page) is False


def test_source_page_is_never_its_own_target(smartling_job):
    assert is_in_flight_target(source_page_of(smartling_job)) is False


def test_a_translated_page_is_not_also_a_source(smartling_job):
    fr_page = target_page_of(smartling_job.translations.get())

    state = get_pending_translation_state(fr_page)

    assert state.is_target is True
    assert state.locales == []


def test_the_same_locale_in_two_pending_jobs_is_named_once(smartling_job, superuser):
    """
    Re-submitting a page can leave two outstanding jobs covering the same locale,
    and callers name those locales to the user, so never "French, French".
    """
    translation = smartling_job.translations.get()
    now = timezone.now()
    second_job = Job.objects.create(
        project=smartling_job.project,
        translation_source=smartling_job.translation_source,
        user=superuser,
        name="Second job for the same page",
        status=JobStatus.AWAITING_AUTHORIZATION,
        # The check constraint requires all three for any status but UNSYNCED.
        first_synced_at=now,
        last_synced_at=now,
        translation_job_uid="second-job-uid",
    )
    second_job.translations.set([translation])

    assert language_codes(get_in_flight_locales_for_instance(source_page_of(smartling_job))) == ["fr"]


def test_locales_come_back_in_a_stable_order(smartling_job_multi_locale):
    assert language_codes(get_in_flight_locales_for_instance(source_page_of(smartling_job_multi_locale))) == [
        "de",
        "fr",
    ]


def test_the_state_costs_exactly_one_query(smartling_job_multi_locale, django_assert_num_queries):
    page = source_page_of(smartling_job_multi_locale)

    with django_assert_num_queries(1):
        get_pending_translation_state(page)


def test_a_page_with_no_smartling_history_also_costs_one_query(root_page, locales, django_assert_num_queries):
    page = InfoPageFactory(parent=root_page, title="Never translated")

    with django_assert_num_queries(1):
        get_pending_translation_state(page)


def test_the_mixin_is_not_a_model():
    """The mixin must generate no migration wherever it is applied."""
    assert not issubclass(PendingTranslationLockMixin, models.Model)


def test_no_outstanding_job_means_no_lock(root_page, locales):
    page = InfoPageFactory(parent=root_page, title="Never translated")
    assert not Job.objects.filter(translation_source__object_id=page.translation_key).exists()
    assert page.get_lock() is None


def test_outstanding_job_locks_the_source_page(smartling_job):
    lock = source_page_of(smartling_job).get_lock()

    assert isinstance(lock, PendingTranslationLock)
    assert lock.is_target is False
    assert language_codes(lock.locales) == ["fr"]


def test_outstanding_job_locks_the_translated_page(smartling_job):
    fr_page = target_page_of(smartling_job.translations.get())

    lock = fr_page.get_lock()

    assert isinstance(lock, PendingTranslationLock)
    assert lock.is_target is True


def test_scheduled_publish_lock_is_not_shadowed(smartling_job):
    """
    ScheduledForPublishLock blocks everyone, and carries the "Cancel scheduled publish"
    button; shadowing it would grant that edit and drop the button.
    """
    page = source_page_of(smartling_job)
    page.save_revision(approved_go_live_at=timezone.now() + timedelta(days=1))
    # Refetch rather than refresh_from_db(), which leaves the cached
    # scheduled_revision property in place.
    page = type(page).objects.get(pk=page.pk)

    assert isinstance(page.get_lock(), ScheduledForPublishLock)


def test_manual_lock_is_not_shadowed(smartling_job, regular_user):
    page = source_page_of(smartling_job)
    page.locked = True
    page.locked_by = regular_user
    page.locked_at = timezone.now()
    page.save()
    page.refresh_from_db()

    assert isinstance(page.get_lock(), BasicLock)


def test_lock_blocks_a_regular_user_and_not_a_superuser(smartling_job, regular_user, superuser):
    """
    The lock object exists for both users, which is what renders the banner, but
    applies to only one, which is what refuses the save.
    """
    lock = source_page_of(smartling_job).get_lock()

    assert lock.for_user(regular_user) is True
    assert lock.for_user(superuser) is False


def test_the_project_decides_who_may_bypass(smartling_job, regular_user, superuser, monkeypatch):
    """A model that overrides the hook gets its own policy, up to letting nobody through."""
    page = source_page_of(smartling_job)
    monkeypatch.setattr(type(page), "can_bypass_pending_translation_lock", lambda self, user: False)

    lock = page.get_lock()

    assert lock.for_user(superuser) is True
    assert lock.for_user(regular_user) is True

    monkeypatch.setattr(type(page), "can_bypass_pending_translation_lock", lambda self, user: True)

    lock = page.get_lock()

    assert lock.for_user(superuser) is False
    assert lock.for_user(regular_user) is False


def test_get_lock_is_memoised(smartling_job):
    """
    The edit view, the status side panel and PagePermissionTester each reach
    get_lock() during one request, so the repeat call must not re-run our query.
    """
    page = source_page_of(smartling_job)
    plain = InfoPageFactory(parent=page.get_parent(), title="No Smartling history")

    page.get_lock()
    plain.get_lock()

    with CaptureQueriesContext(connection) as repeated_with_job:
        page.get_lock()
    with CaptureQueriesContext(connection) as repeated_without_job:
        plain.get_lock()

    assert [q for q in repeated_with_job.captured_queries if "smartling" in q["sql"]] == []
    assert [q for q in repeated_without_job.captured_queries if "smartling" in q["sql"]] == []


def test_editor_and_superuser_get_different_messages(smartling_job, regular_user, superuser):
    lock = source_page_of(smartling_job).get_lock()

    editor_message = str(lock.get_message(regular_user))
    superuser_message = str(lock.get_message(superuser))

    assert editor_message != superuser_message
    assert "French" in editor_message
    assert "French" in superuser_message
    # The editor is told the page is locked, and why.
    assert "can't be edited" in editor_message
    assert "until that translation comes back" in editor_message
    # The bypassing user is told the consequence of going ahead anyway.
    assert "mismatch" in superuser_message


def test_the_message_follows_the_bypass_policy_not_is_superuser(smartling_job, superuser, monkeypatch):
    """A superuser the project does not let through is being held like anyone else."""
    page = source_page_of(smartling_job)
    monkeypatch.setattr(type(page), "can_bypass_pending_translation_lock", lambda self, user: False)

    message = str(page.get_lock().get_message(superuser))

    assert "can't be edited" in message


def test_messages_name_every_locale_in_flight(smartling_job_multi_locale, superuser):
    lock = source_page_of(smartling_job_multi_locale).get_lock()

    message = str(lock.get_message(superuser))

    assert "French" in message
    assert "German" in message


def test_translated_page_message_is_about_being_overwritten(smartling_job, regular_user):
    fr_page = target_page_of(smartling_job.translations.get())

    message = str(fr_page.get_lock().get_message(regular_user))

    assert "overwritten" in message


def test_lock_furniture(smartling_job, regular_user):
    lock = source_page_of(smartling_job).get_lock()

    assert str(lock.get_locked_by(regular_user)) == "Being translated"
    assert lock.get_icon(regular_user) == "wagtail-localize-language"
    assert "French" in str(lock.get_description(regular_user))


def test_an_anonymous_user_is_locked_out(smartling_job):
    lock = source_page_of(smartling_job).get_lock()
    assert lock.for_user(AnonymousUser()) is True
