import json
import re

from unittest.mock import Mock

import pytest

from wagtail.models import Locale
from wagtail_localize.models import Translation, TranslationSource

from wagtail_localize_smartling.api.client import InvalidResponse, client
from wagtail_localize_smartling.exceptions import IncapableVisualContextCallback
from wagtail_localize_smartling.models import Job

from testapp.factories import InfoPageFactory, TranslationSourceFactory
from tests.factories import JobFactory


pytestmark = pytest.mark.django_db


def get_authorized_locales_from_request_body(request):
    """The localeIdsToAuthorize[] values of a multipart file-upload request, in the order sent."""
    return re.findall(r'name="localeIdsToAuthorize\[\]"\r\n\r\n(.*?)\r\n', request.body.decode(errors="ignore"))


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


def test_client__download_translations(smartling_job: "Job", smartling_translations_zip):
    """The zip is fetched for one file of the job, under that file's own URI."""
    job_file = smartling_job.files.get()
    smartling_translations_zip(file_uri=job_file.file_uri, entries={f"fr/{job_file.file_uri}": 'msgid ""\nmsgstr ""\n'})

    with client.download_translations(job_file=job_file) as translations_zip:
        assert translations_zip.namelist() == [f"fr/{job_file.file_uri}"]


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
    # Very similar to test_client__add_html_context_to_job__depends_on_callback_availability
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
    # Fake a validation error using the smartling_add_visual_context__error_response fixture
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
    batch_uid = client.create_batch_for_job(job=smartling_job, job_files=smartling_job.files.all())
    assert batch_uid == "dummy_batch_uid"


def test_client__create_batch_for_job__error_path(
    smartling_job: "Job",
    smartling_create_batch_for_job__error_response,
):
    with pytest.raises(InvalidResponse) as exc:
        client.create_batch_for_job(job=smartling_job, job_files=smartling_job.files.all())

    assert exc.value.args == (
        "Response did not match expected format: {'response': {'code': 'VALIDATION_ERROR', 'data': [{'key': 'some batch key', 'message': 'some batch message', 'details': 'some batch details'}]}}",  # noqa: E501
    )


def test_client__upload_file_to_job_batch__happy_path(
    smartling_job,
    smartling_upload_files_to_job_batch,
):
    client.upload_file_to_job_batch(job_file=smartling_job.files.get(), batch_uid="test-batch-uid")


def test_client__upload_file_to_job_batch__error_path(
    smartling_job,
    smartling_upload_files_to_job_batch__error_response,
):
    with pytest.raises(InvalidResponse) as exc:
        client.upload_file_to_job_batch(job_file=smartling_job.files.get(), batch_uid="test-batch-uid")

    assert exc.value.args == (
        "Response did not match expected format: {'response': {'code': 'VALIDATION_ERROR', 'data': [{'key': 'some upload key', 'message': 'some upload message', 'details': 'some upload details'}]}}",  # noqa: E501
    )


def test_file_uri_is_per_file_not_per_job(root_page):
    # A source the job never sends, so that file ids and source ids do not
    # line up and a URI built from the wrong one of the two cannot pass by luck.
    TranslationSourceFactory(source_instance=InfoPageFactory(parent=root_page, title="A page nobody sent"))

    job = JobFactory(
        source_instance=root_page,
        files=[
            TranslationSourceFactory(source_instance=root_page),
            TranslationSourceFactory(source_instance=InfoPageFactory(parent=root_page, title="Second page")),
        ],
    )
    first_file, second_file = job.files.all()

    assert (
        client.get_file_uri_for_job_file(job_file=first_file)
        == f"job_{job.pk}_ts_{first_file.translation_source_id}.po"
    )
    assert (
        client.get_file_uri_for_job_file(job_file=second_file)
        == f"job_{job.pk}_ts_{second_file.translation_source_id}.po"
    )


def test_upload_sends_the_po_of_the_file_whose_uri_it_names(root_page, smartling_upload_files_to_job_batch, responses):
    """The bytes and the URI have to come from the same source, or Smartling namespaces them wrongly."""
    ordered_page = InfoPageFactory(parent=root_page, title="The page that was ordered")
    ordered_source, _ = TranslationSource.get_or_create_from_instance(ordered_page)
    job = JobFactory(source_instance=root_page, files=[ordered_source])

    client.upload_file_to_job_batch(job_file=job.files.get(), batch_uid="test-batch-uid")

    uploaded = responses.calls[-1].request.body.decode(errors="ignore")
    assert f"job_{job.pk}_ts_{ordered_source.pk}.po" in uploaded
    assert "The page that was ordered" in uploaded


def test_upload_authorizes_only_the_locales_this_file_asked_for(
    root_page, smartling_upload_files_to_job_batch, responses
):
    """
    Uploading requests only the locales that each particular page needs.

    Authorizing the whole job's locales would request every locale for every
    page, which is not correct.
    """
    ordered_source = TranslationSourceFactory(source_instance=root_page)
    other_source = TranslationSourceFactory(source_instance=InfoPageFactory(parent=root_page, title="Second page"))
    job = JobFactory(source_instance=root_page, files=[ordered_source, other_source], translations=[])
    job.translations.set(
        [
            Translation.objects.create(source=ordered_source, target_locale=Locale.objects.get(language_code="fr")),
            Translation.objects.create(source=other_source, target_locale=Locale.objects.get(language_code="de")),
        ]
    )

    client.upload_file_to_job_batch(
        job_file=job.files.get(translation_source=ordered_source), batch_uid="test-batch-uid"
    )

    assert get_authorized_locales_from_request_body(responses.calls[-1].request) == ["fr"]


def test_uploading_a_file_with_no_authorized_locales_is_refused(root_page, smartling_upload_files_to_job_batch):
    job = JobFactory(source_instance=root_page, translations=[])
    job_file = job.files.get()
    # Nobody ordered this file's page in any locale, which is what makes the
    # upload refusable rather than the job merely being empty.
    assert not job_file.job.translations.filter(source=job_file.translation_source).exists()

    with pytest.raises(ValueError):
        client.upload_file_to_job_batch(job_file=job_file, batch_uid="test-batch-uid")


def test_create_batch_declares_only_the_files_it_is_given(root_page, smartling_create_batch_for_job, responses):
    """A push that is finishing an interrupted one declares what it is about to upload, and nothing else."""
    job = JobFactory(
        source_instance=root_page,
        synced=True,
        files=[
            TranslationSourceFactory(source_instance=root_page),
            TranslationSourceFactory(source_instance=InfoPageFactory(parent=root_page, title="Second page")),
        ],
    )
    still_to_upload = job.files.last()

    client.create_batch_for_job(job=job, job_files=[still_to_upload])

    body = json.loads(responses.calls[-1].request.body)
    assert body["fileUris"] == [f"job_{job.pk}_ts_{still_to_upload.translation_source_id}.po"]


# =============================================================================
# Per-locale API methods (Issues #28 and #37)
# =============================================================================


def test_client__get_file_status_for_locale(
    smartling_job: "Job",
    smartling_get_file_status,
):
    """Test getting file status for a specific locale."""
    job_file = smartling_job.files.get()
    # Keyed on the file's own URI, which the smartling_job fixture deliberately
    # makes different from the job's.
    smartling_get_file_status("fr", total_strings=10, completed_strings=8, file_uri=job_file.file_uri)

    result = client.get_file_status_for_locale(job_file=job_file, locale_id="fr")

    assert result["totalStringCount"] == 10
    assert result["completedStringCount"] == 8


def test_client__download_translation_for_locale(
    smartling_job: "Job",
    smartling_download_translation_for_locale,
):
    """Test downloading translation for a specific locale."""
    po_content = """
msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"

msgid "Test"
msgstr "Test traduit"
"""
    job_file = smartling_job.files.get()
    smartling_download_translation_for_locale("fr", po_content, file_uri=job_file.file_uri)

    result = client.download_translation_for_locale(job_file=job_file, locale_id="fr")

    assert b"Test traduit" in result


def test_client__add_locale_to_job(
    smartling_job: "Job",
    smartling_add_locale_to_job,
):
    """Test adding a locale to an existing job."""
    smartling_add_locale_to_job("job_to_be_cancelled", "de")

    # The API returns None on success (data: null in the response)
    result = client.add_locale_to_job(job=smartling_job, locale_id="de")

    assert result is None
