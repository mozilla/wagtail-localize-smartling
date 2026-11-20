import pytest

from django.core.management import call_command

from wagtail_localize_smartling.api.types import JobStatus

from testapp.factories import InfoPageFactory
from tests.factories import JobFactory


@pytest.mark.skip("WRITE ME")
@pytest.mark.django_db()
def test_sync_smartling(smartling_project):
    unsynced_job_page = InfoPageFactory()
    JobFactory(source_instance=unsynced_job_page, unsynced=True)

    call_command("sync_smartling")

    raise AssertionError("TODO")


@pytest.mark.django_db()
def test_sync_smartling_skips_a_job_that_never_reached_smartling(root_page, locales, smartling_project, mocker):
    # Create an unsynced Job that never reached Smartling and is blocked.
    JobFactory(source_instance=root_page, unsynced=True, sync_blocked=True)
    # Create a (non-blocked) uncynced Job.
    live_job = JobFactory(source_instance=root_page, unsynced=True)

    sync_job_function = mocker.patch("wagtail_localize_smartling.management.commands.sync_smartling.sync_job")

    call_command("sync_smartling")

    assert [call.args[0] for call in sync_job_function.call_args_list] == [live_job.pk]


@pytest.mark.django_db()
def test_sync_smartling_still_polls_a_blocked_job_that_reached_smartling(root_page, locales, smartling_project, mocker):
    """
    If a Job's files reached Smartling, then sync_smartling does not skip it.

    Even if the Job is blocked, its files are already in Smartling, so
    sync_smartling does not exclude it.
    """
    blocked_job = JobFactory(
        source_instance=root_page,
        synced=True,
        status=JobStatus.IN_PROGRESS,
        sync_blocked=True,
    )
    sync_job_function = mocker.patch("wagtail_localize_smartling.management.commands.sync_smartling.sync_job")

    call_command("sync_smartling")

    assert [call.args[0] for call in sync_job_function.call_args_list] == [blocked_job.pk]
