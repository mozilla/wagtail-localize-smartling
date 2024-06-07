import pytest

from django.core.management import call_command

from testapp.factories import InfoPageFactory
from tests.factories import JobFactory


@pytest.mark.skip()
@pytest.mark.django_db()
def test_sync_smartling(smartling_project):
    unsynced_job_page = InfoPageFactory()
    JobFactory(source_instance=unsynced_job_page, unsynced=True)

    call_command("sync_smartling")

    raise AssertionError("TODO")
