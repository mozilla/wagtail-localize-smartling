from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest

from wagtail_localize_smartling.api.client import InvalidResponse, client
from wagtail_localize_smartling.exceptions import IncapableVisualContextCallback
from wagtail_localize_smartling.models import Job


pytestmark = pytest.mark.django_db


@pytest.mark.skip("WRITE ME")
def test_client__get_project_details():
    pass


@pytest.mark.skip("WRITE ME")
def test_client__create_job():
    pass


@pytest.mark.skip("WRITE ME")
def test_client__list_jobs():
    pass


@pytest.mark.skip("WRITE ME")
def test_client__get_job_details():
    pass


@pytest.mark.skip("WRITE ME")
def test_client__upload_po_file_for_job():
    pass


@pytest.mark.skip("WRITE ME")
def test_client__download_translations():
    pass


@pytest.mark.parametrize("bootstrap_callback", (True, False))
def test_client__add_html_context_to_job__depends_on_callback_availability(
    bootstrap_callback,
    smartling_job: "Job",
    smartling_settings,
    smartling_add_visual_context,
):
    callback_func = Mock(
        name="fake callback",
        return_value=(
            "https://example.com/path/to/page/",
            "<html><body>test</body></html>",
        ),
    )

    if bootstrap_callback:
        smartling_settings.VISUAL_CONTEXT_CALLBACK = callback_func

    client.add_html_context_to_job(job=smartling_job)

    if bootstrap_callback:
        callback_func.assert_called_once_with(smartling_job)
    else:
        assert not callback_func.called


def test_client__add_html_context_to_job__happy_path(
    smartling_job: "Job",
    smartling_settings,
    smartling_add_visual_context,
):
    # Very similar to test_client__add_html_context_to_job__depends_on_callback_availability  # noqa: E501
    # but separate for regression-protection value
    callback_func = Mock(
        name="fake callback",
        return_value=(
            "https://example.com/path/to/page/",
            "<html><body>test</body></html>",
        ),
    )

    smartling_settings.VISUAL_CONTEXT_CALLBACK = callback_func
    resp = client.add_html_context_to_job(job=smartling_job)
    callback_func.assert_called_once_with(smartling_job)
    assert resp == {"processUid": "dummy_process_uid"}


def test_client__add_html_context_to_job__callback_does_not_provide_visual_context_data(
    smartling_job: "Job",
    smartling_settings,
    smartling_add_visual_context,
):
    callback_func = Mock(
        name="fake callback",
        side_effect=IncapableVisualContextCallback("Testing!"),
    )

    smartling_settings.VISUAL_CONTEXT_CALLBACK = callback_func
    resp = client.add_html_context_to_job(job=smartling_job)
    callback_func.assert_called_once_with(smartling_job)
    assert resp is None


def test_client__add_html_context_to_job__error_path(
    smartling_job: "Job",
    smartling_settings,
    smartling_add_visual_context__error_response,
):
    # Fake a validation error using the smartling_add_visual_context__error_response fixture  # noqa: E501
    callback_func = Mock(
        name="fake callback",
        return_value=(
            "https://example.com/path/to/page/",
            "<html><body>test</body></html>",
        ),
    )

    smartling_settings.VISUAL_CONTEXT_CALLBACK = callback_func

    with pytest.raises(InvalidResponse) as exc:
        client.add_html_context_to_job(job=smartling_job)

    assert exc.value.args == (
        "Response did not match expected format: {'response': {'code': 'VALIDATION_ERROR', 'data': [{'key': 'some key', 'message': 'some message', 'details': 'some details'}]}}",  # noqa: E501
    )

    callback_func.assert_called_once_with(smartling_job)


def test_client__create_batch_for_job__happy_path(
    smartling_job: "Job",
    smartling_create_batch_for_job,
):
    batch_uid = client.create_batch_for_job(job=smartling_job)
    assert batch_uid == "dummy_batch_uid"


def test_client__create_batch_for_job__error_path(
    smartling_job: "Job",
    smartling_create_batch_for_job__error_response,
):
    with pytest.raises(InvalidResponse) as exc:
        client.create_batch_for_job(job=smartling_job)

    assert exc.value.args == (
        "Response did not match expected format: {'response': {'code': 'VALIDATION_ERROR', 'data': [{'key': 'some batch key', 'message': 'some batch message', 'details': 'some batch details'}]}}",  # noqa: E501
    )


def test_client__upload_files_to_job_batch__happy_path(
    smartling_job,
    smartling_upload_files_to_job_batch,
):
    client.upload_files_to_job_batch(job=smartling_job, batch_uid="test-batch-uid")


def test_client__upload_files_to_job_batch__error_path(
    smartling_job,
    smartling_upload_files_to_job_batch__error_response,
):
    with pytest.raises(InvalidResponse) as exc:
        client.upload_files_to_job_batch(job=smartling_job, batch_uid="test-batch-uid")

    assert exc.value.args == (
        "Response did not match expected format: {'response': {'code': 'VALIDATION_ERROR', 'data': [{'key': 'some upload key', 'message': 'some upload message', 'details': 'some upload details'}]}}",  # noqa: E501
    )


def test_get_file_uri_for_job():
    mock_job = Mock(spec=Job)
    mock_job.pk = 23
    mock_job.translation_source.pk = 45
    assert client.get_file_uri_for_job(job=mock_job) == "job_23_ts_45.po"
