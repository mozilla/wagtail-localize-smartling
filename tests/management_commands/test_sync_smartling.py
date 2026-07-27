# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import pytest

from django.core.management import call_command

from testapp.factories import InfoPageFactory
from tests.factories import JobFactory


@pytest.mark.skip("WRITE ME")
@pytest.mark.django_db()
def test_sync_smartling(smartling_project):
    unsynced_job_page = InfoPageFactory()
    JobFactory(source_instance=unsynced_job_page, unsynced=True)

    call_command("sync_smartling")

    raise AssertionError("TODO")
