import pytest

from django.core.management import call_command

from testapp.factories import InfoPageFactory
from tests.factories import JobFactory


@pytest.mark.django_db()
def test_sync_smartling(smartling_settings):
    page = InfoPageFactory()
    unsynced_job = JobFactory(source_instance=page, unsynced=True)

    call_command("sync_smartling")

    # TODO mock job creation response
    # TODO mock file upload response (200)
    # TODO mock file added to job response (200 + 202)
    raise AssertionError("TODO")
