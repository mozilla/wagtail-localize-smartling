import pytest

from django.core.management import call_command

from wagtail_localize_smartling.api.types import JobStatus
from wagtail_localize_smartling.models import Job

from testapp.factories import InfoPageFactory
from tests.factories import JobFactory


pytestmark = pytest.mark.django_db


@pytest.mark.skip("WRITE ME")
def test_sync_smartling(smartling_project):
    unsynced_job_page = InfoPageFactory()
    JobFactory(source_instance=unsynced_job_page, unsynced=True)

    call_command("sync_smartling")

    raise AssertionError("TODO")


def test_sync_smartling_skips_a_job_that_never_reached_smartling(root_page, locales, smartling_project, mocker):
    # Create an unsynced Job that never reached Smartling and is blocked.
    JobFactory(source_instance=root_page, unsynced=True, sync_blocked=True)
    # Create a (non-blocked) uncynced Job.
    live_job = JobFactory(source_instance=root_page, unsynced=True)

    sync_job_function = mocker.patch("wagtail_localize_smartling.management.commands.sync_smartling.sync_job")

    call_command("sync_smartling")

    assert [call.args[0] for call in sync_job_function.call_args_list] == [live_job.pk]


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


def test_sync_smartling_sweeps_on_after_a_job_fails_to_record_its_failure(
    root_page, locales, smartling_project, mocker
):
    """A Job deleted while the sweep runs must not cost every Job queued behind it."""
    JobFactory(source_instance=root_page, unsynced=True)
    JobFactory(source_instance=InfoPageFactory(), unsynced=True)
    sync_function = mocker.patch(
        "wagtail_localize_smartling.sync._initial_sync",
        side_effect=TimeoutError("smartling unreachable"),
    )
    mocker.patch.object(
        Job,
        "record_sync_failure",
        side_effect=Job.NotUpdated("Save with update_fields did not affect any rows."),
    )

    call_command("sync_smartling")

    assert sync_function.call_count == 2
