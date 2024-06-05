import pytest

from django.core.management import call_command

from testapp.factories import InfoPageFactory
from tests.factories import JobFactory


@pytest.mark.django_db()
def test_sync_smartling():
    unsynced_job_page = InfoPageFactory()
    unsynced_job = JobFactory(source_instance=unsynced_job_page, unsynced=True)

    call_command("sync_smartling")

    raise AssertionError("TODO")
