import logging
import pprint
import textwrap

from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timedelta
from functools import cached_property
from io import BytesIO
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    TypeVar,
    cast,
)
from urllib.parse import quote, urljoin
from zipfile import ZipFile

import requests
import requests.exceptions
import rest_framework.serializers

from django.utils import timezone
from django.utils.functional import SimpleLazyObject
from requests.exceptions import HTTPError

from .. import utils
from ..exceptions import IncapableVisualContextCallback
from ..settings import settings as smartling_settings
from . import types
from .serializers import (
    AddVisualContextToJobSerializer,
    AuthenticateResponseSerializer,
    CreateBatchResponseSerializer,
    CreateJobResponseSerializer,
    GetJobDetailsResponseSerializer,
    GetProjectDetailsResponseSerializer,
    ListJobsResponseSerializer,
    NullDataResponseSerializer,
    RefreshAccessTokenResponseSerializer,
    ResponseSerializer,
    UploadFileToBatchResponseSerializer,
)


if TYPE_CHECKING:
    from ..models import Job


logger = logging.getLogger(__name__)


# Exception classes


class SmartlingAPIError(Exception):
    """
    Base class for exceptions raised by the client.
    """


class InvalidResponse(SmartlingAPIError):
    """
    Exception to be raised when the response isn't in the format we expect.
    """


class FailedResponse(SmartlingAPIError):
    """
    Exception to be raised when we get a valid JSON response, but the request was
    unsuccessful for some reason.
    """

    def __init__(self, *, code: str, errors: list[types.SmartlingAPIErrorDict]):
        self.code = code
        self.errors = errors

    def __str__(self):
        return (
            f"{self.code}\n"
            f"{textwrap.indent(pprint.pformat(self.errors, indent=2), prefix='  ')}"
        )


class JobNotFound(SmartlingAPIError):
    pass


# API client

# TODO allow customization of serializers to account for custom fields

RD = TypeVar("RD", bound=dict)


class SmartlingAPIClient:
    def __init__(self):
        self.token_type: str = "Bearer"
        self.access_token: str | None = None
        self.refresh_token: str | None = None

        # Set expiry times in the past, initially
        a_day_ago = timezone.now() - timedelta(days=1)
        self.access_token_expires_at: datetime = a_day_ago
        self.refresh_token_expires_at: datetime = a_day_ago

    # Utilities

    @property
    def _headers(self) -> dict[str, str]:
        now = timezone.now()
        if self.access_token is None or (self.access_token_expires_at <= now):
            if self.refresh_token is not None and self.refresh_token_expires_at > now:
                self._refresh_access_token()
            else:
                self._authenticate()

        return {"Authorization": f"{self.token_type} {self.access_token}"}

    def _update_tokens(
        self,
        *,
        access_token: str,
        refresh_token: str,
        access_token_expires_in: int,
        refresh_token_expires_in: int,
        token_type: str,
    ):
        now = timezone.now()

        # Set the tokens and token type
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.token_type = token_type

        # Set the expiry times, but knock 10% off the expiry periods to give
        # ourselves a bit of wiggle room and reduce the likelihood of trying to
        # use expired creds
        self.access_token_expires_at = now + timedelta(
            seconds=0.9 * access_token_expires_in
        )
        self.refresh_token_expires_at = now + timedelta(
            seconds=0.9 * refresh_token_expires_in
        )

    def _authenticate(self):
        data = cast(
            types.AuthenticateResponseData,
            self._request(
                method="POST",
                path="/auth-api/v2/authenticate",
                response_serializer_class=AuthenticateResponseSerializer,
                send_headers=False,
                json={
                    "userIdentifier": smartling_settings.USER_IDENTIFIER,
                    "userSecret": smartling_settings.USER_SECRET,
                },
            ),
        )
        self._update_tokens(
            access_token=data["accessToken"],
            refresh_token=data["refreshToken"],
            access_token_expires_in=data["expiresIn"],
            refresh_token_expires_in=data["refreshExpiresIn"],
            token_type=data["tokenType"],
        )

    def _refresh_access_token(self):
        data = cast(
            types.RefreshAccessTokenResponseData,
            self._request(
                method="POST",
                path="/auth-api/v2/authenticate/refresh",
                response_serializer_class=RefreshAccessTokenResponseSerializer,
                send_headers=False,
                json={"refreshToken": self.refresh_token},
            ),
        )
        self._update_tokens(
            access_token=data["accessToken"],
            refresh_token=data["refreshToken"],
            access_token_expires_in=data["expiresIn"],
            refresh_token_expires_in=data["refreshExpiresIn"],
            token_type=data["tokenType"],
        )

    @cached_property
    def _base_url(self) -> str:
        if smartling_settings.ENVIRONMENT == "production":
            return "https://api.smartling.com"
        elif smartling_settings.ENVIRONMENT == "staging":
            return "https://api.stg.smartling.net"
        raise SmartlingAPIError(
            f"Unknown environment: {smartling_settings.ENVIRONMENT}"
        )

    def _request(
        self,
        *,
        method: Literal["GET", "POST"],
        path: str,
        response_serializer_class: type[
            ResponseSerializer | NullDataResponseSerializer
        ],
        send_headers: bool = True,
        **kwargs,
    ) -> dict[str, Any]:
        url = urljoin(self._base_url, path)
        headers = self._headers if send_headers else {}

        logger.info(
            "Smartling API request: %s %s",
            method,
            url,
        )
        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            timeout=smartling_settings.API_TIMEOUT_SECONDS,
            **kwargs,
        )
        logger.info(
            "Smartling API response: %s %s",
            response.status_code,
            f"{response.elapsed.total_seconds()}s",
        )

        try:
            response_json = response.json()
        except requests.exceptions.JSONDecodeError as e:
            raise InvalidResponse(
                f"Response was not valid JSON: {response.text}"
            ) from e

        serializer = cast(
            # This cast is required because the created instance could be a
            # ListSerializer if we'd passed many=True, but we know better.
            response_serializer_class,  # pyright: ignore [reportInvalidTypeForm]
            response_serializer_class(data=response_json),
        )
        try:
            serializer.is_valid(raise_exception=True)
        except rest_framework.serializers.ValidationError as e:
            raise InvalidResponse(
                f"Response did not match expected format: {serializer.initial_data}"
            ) from e

        try:
            response.raise_for_status()
        except HTTPError as e:
            code, errors = serializer.response_errors
            raise FailedResponse(code=code, errors=errors) from e

        return serializer.response_data

    # API methods

    def get_project_details(
        self, *, include_disabled_locales: bool = True
    ) -> types.GetProjectDetailsResponseData:
        params = {}
        if include_disabled_locales:
            params["includeDisabledLocales"] = "true"
        return cast(
            types.GetProjectDetailsResponseData,
            self._request(
                method="GET",
                path=f"/projects-api/v2/projects/{quote(smartling_settings.PROJECT_ID)}",
                response_serializer_class=GetProjectDetailsResponseSerializer,
                params=params,
            ),
        )

    def list_jobs(self, *, name: str | None = None) -> types.ListJobsResponseData:
        params = {}
        if name is not None:
            params["jobName"] = name
        return cast(
            types.ListJobsResponseData,
            self._request(
                method="GET",
                path=f"/jobs-api/v3/projects/{quote(smartling_settings.PROJECT_ID)}/jobs",
                response_serializer_class=ListJobsResponseSerializer,
                params=params,
            ),
        )

    def create_job(
        self,
        *,
        job_name: str,
        target_locale_ids: list[str] | None = None,
        description: str | None = None,
        reference_number: str | None = None,
        due_date: datetime | None = None,
        callback_url: str | None = None,
        callback_method: Literal["GET", "POST"] | None = None,
    ) -> types.CreateJobResponseData:
        if (callback_url is None) != (callback_method is None):
            raise ValueError(
                "Both callback_url and callback_method must be provided, or neither"
            )

        params: dict[str, Any] = {
            "jobName": job_name,
        }
        if target_locale_ids is not None:
            params["targetLocaleIds"] = target_locale_ids
        if description is not None:
            params["description"] = description
        if reference_number is not None:
            params["referenceNumber"] = reference_number
        if due_date is not None:
            params["dueDate"] = due_date.isoformat()
        if callback_url is not None:
            params["callbackMethod"] = callback_method
            params["callbackUrl"] = callback_url

        return cast(
            types.CreateJobResponseData,
            self._request(
                method="POST",
                path=f"/jobs-api/v3/projects/{quote(smartling_settings.PROJECT_ID)}/jobs",
                response_serializer_class=CreateJobResponseSerializer,
                json=params,
            ),
        )

    def get_job_details(self, *, job: "Job") -> types.GetJobDetailsResponseData:
        try:
            return cast(
                types.GetJobDetailsResponseData,
                self._request(
                    method="GET",
                    path=f"/jobs-api/v3/projects/{quote(smartling_settings.PROJECT_ID)}/jobs/{quote(job.translation_job_uid)}",
                    response_serializer_class=GetJobDetailsResponseSerializer,
                ),
            )
        except FailedResponse as e:
            if e.code == "NOT_FOUND_ERROR":
                raise JobNotFound(f"Job {job.translation_job_uid} not found") from e
            else:
                raise

    def get_file_uri_for_job(self, *, job: "Job") -> str:
        # One Wagtail content object (Page or Snippet, usually) mapes to one
        # Smartling Job containing one .po file, and we use Job to get a URI
        # for that PO file.

        # Why are we using the Job PK as part of the file URI, rather than just
        # "file.po" or something fixed?
        # It's because Smartling uses this as the default namespace for any
        # strings contained in the file, and strings can only exist once in
        # a namespace. If we were to upload the same file to multiple Jobs
        # (e.g. if a page got translated, converted back to an alias and
        # then translated again), then the second Job would contain no
        # strings because they're all part of the, past, first Job. You can't
        # authorize a Job with no strings, so it'd be effectively stuck.
        # (This is something we may need to watch out for in the future, too.)

        # NB: This HAS to be deterministic and stable, so that it can be
        # called at any point for the given Job to get the same value back.

        # Here, we're combining the Job ID and its TranslationSource to try to
        # make this more unique and traceable. If we need greater uniqueness,
        # we can add in (part of) the UUID-sourced string from
        # job.translation_source.object.translation_key.hex or pass in a
        # salt of some kind that's still deterministic based on the state
        # of the Wagtail object - such as a hash of JSON content

        file_uri = f"job_{job.pk}_ts_{job.translation_source.pk}.po"
        logger.info(f"Generated file_uri {file_uri}")
        return file_uri

    def create_batch_for_job(self, *, job: "Job") -> str:
        # Create a Batch for uploading files to the given Job,
        # specifying the file(s) upfront.
        #
        # Returns the Batch UID, which we'll need to upload our file(s) to the batch

        body: dict[str, Any] = {
            "authorize": False,
            "translationJobUid": job.translation_job_uid,
            "fileUris": [
                # NB we just send one file per Job
                self.get_file_uri_for_job(job=job),
            ],
            # Not sending "localeWorkflows" key/value pair - doesn't look like
            # we really need them. If we do, that would need us to maintain
            # a map of language codes to workflow IDs in configuration,
            # drawing on data manually extracted from Smartling's web UI
            # "localeWorkflows": [
            #     {
            #         "targetLocaleId": "xx-YY",
            #         "workflowUid": "SET ME",
            #     },
            #     ...
            # ],
        }

        result = self._request(
            method="POST",
            path=f"/job-batches-api/v2/projects/{quote(job.project.project_id)}/batches",
            response_serializer_class=CreateBatchResponseSerializer,
            json=body,
        )

        return result["batchUid"]

    def upload_files_to_job_batch(self, *, job: "Job", batch_uid: str) -> str:
        file_uri = self.get_file_uri_for_job(job=job)

        locales_to_authorize = [
            utils.format_smartling_locale_id(t.target_locale.language_code)
            for t in job.translations.all()
        ]

        data_payload = {
            # NB: If we switch to multiple files per Batch (e.g. different
            # locales per upload to the batch, for some reason), the fileUri must
            # be unique _per Batch_. With a single file, it's not a concern.
            "fileUri": file_uri,
            "fileType": "gettext",
            "localeIdsToAuthorize[]": locales_to_authorize,
        }

        file_payload = {
            "file": (file_uri, str(job.translation_source.export_po())),
        }

        self._request(
            method="POST",
            path=f"/job-batches-api/v2/projects/{quote(job.project.project_id)}/batches/{batch_uid}/file",
            response_serializer_class=UploadFileToBatchResponseSerializer,
            files=file_payload,
            data=data_payload,
        )
        return file_uri

    def add_html_context_to_job(self, *, job: "Job"):
        """
        To help with translation, Smartling supports the idea of a
        "visual context" for translators, which effectively gives them
        a real-time/WYSIWYG view of the page they are translating.

        We push info about the context, then trigger its processing, via
        a special combined-action API endpoint:
        https://api-reference.smartling.com/#tag/Context/operation/uploadAndMatchVisualContext

        As for how we get the info to send as a visual context, that is up to the
        implementation that is using wagtail-localize-smartling to decide, via
        the use of a configurable callback function - see `VISUAL_CONTEXT_CALLBACK`
        in the settings or the README.

        If the callback is defined, it will be used to generate the the visual
        context to send to Smartling.

        The callback must take the Job instance and return two values:

        1. A full, absolute URL for the page that shows the content used
           to generate that Job
        2. The HTML of that same page

        e.g.

            from wagtail.models import Page
            from wagtail_localize.models import Job
            from wagtail_localize_smartling.exceptions import IncapableVisualContextCallback

            def get_visual_context(job: Job) -> tuple[str, str]:

                # This assumes the page is live and visible. If the page is a
                # draft, you will need a some custom work to expose the draft
                # version of the page

                content_obj = job.translation_source.get_source_instance()

                # IMPORTANT: if your translatable objects include some where a visual
                # context is not available or appropriate (eg a Snippet, rather than
                # a Page), then your settings.VISUAL_CONTEXT_CALLBACK function should
                # raise IncapableVisualContextCallback with an explaination
                #
                # Below, we simply check if the object is a Page, but depending
                # on how your objects are previewable, you could instead use
                # isinstance(content_obj, PreviewableMixin)

                if not isinstance(content_obj, Page):
                    raise IncapableVisualContextCallback(
                        "Object was not visually previewable"
                    )

                page_url = page.full_url

                html = # code to render that page instance

                return page_url, html

        """  # noqa: E501

        if not (
            visual_context_callback_fn := smartling_settings.VISUAL_CONTEXT_CALLBACK
        ):
            logger.info("No visual context callback configured")
            return

        try:
            url, html = visual_context_callback_fn(job)
        except IncapableVisualContextCallback as ex:
            logger.info(
                "Visual context callback refused to provide values. "
                f"Reason: {str(ex)}. Not sending visual context."
            )
            return

        # data:
        # `name` - url of the page the Job is for
        # `matchparams` - config params for Smartling's string matching
        # `content` - the HTML of the relevant Page for this Job, as bytes

        data_payload: dict[str, Any] = {
            "name": url,
            "matchparams": {
                "translationJobUids": [job.translation_job_uid],
            },
        }

        # The file payload contains the rendered HTML of the page
        # being translated. It needs to be send as multipart form
        # data, so we turn the HTML string into a bytearray
        # and pass it along with a filename based on the slug
        # of the page

        if isinstance(html, str):
            html = bytearray(html, "utf-8")

        filename = utils.get_filename_for_visual_context(url)

        file_payload: dict[str, tuple[str, bytearray, str]] = {
            "content": (filename, html, "text/html"),
        }

        logger.info(
            "Sending visual context to Smartling for Job %s for URL %s",
            job.translation_job_uid,
            url,
        )

        result = self._request(
            method="POST",
            path=f"/context-api/v2/projects/{quote(job.project.project_id)}/contexts/upload-and-match-async",
            response_serializer_class=AddVisualContextToJobSerializer,
            files=file_payload,
            data=data_payload,
        )

        logger.info(
            "Visual context sent. processUid returned: %s", result.get("processUid")
        )

        return result

    @contextmanager
    def download_translations(self, *, job: "Job") -> Generator[ZipFile, None, None]:
        # This is an unusual case where a successful response is a ZIP file,
        # rather than JSON. JSON responses will be returned for errors.

        url = urljoin(
            self._base_url,
            f"/files-api/v2/projects/{quote(job.project.project_id)}/locales/all/file/zip",
        )
        with requests.get(
            url,
            headers=self._headers,
            params={
                "fileUri": job.file_uri,
                "retrievalType": "published",
                "includeOriginalStrings": False,
            },
            stream=True,
            timeout=smartling_settings.API_TIMEOUT_SECONDS,
        ) as response:
            # Log consistently with other requests. Don't log the method and URL
            # until we've initiated the request so it doesn't get interleaved
            # with any auth requests triggered by generating the headers
            logger.info("Smartling API request: GET %s", url)
            logger.info(
                "Smartling API response: %s %s",
                response.status_code,
                f"{response.elapsed.total_seconds()}s",
            )

            # Only 200 responses contain a ZIP file, everything else is an error
            if response.status_code != 200:
                try:
                    response_json = response.json()
                except requests.exceptions.JSONDecodeError as e:
                    raise InvalidResponse(
                        f"Response was not valid JSON: {response.text}"
                    ) from e

                serializer = ResponseSerializer(data=response_json)

                try:
                    serializer.is_valid(raise_exception=True)
                except rest_framework.serializers.ValidationError as e:
                    raise InvalidResponse(
                        f"Response did not match expected format: {serializer.initial_data}"  # noqa: E501
                    ) from e

                try:
                    response.raise_for_status()
                except HTTPError as e:
                    code, errors = serializer.response_errors
                    raise FailedResponse(code=code, errors=errors) from e

            # Ok, cool, the response body is a ZIP file
            # TODO buffer to a temporary file instead of a BytesIO for large files

            buffer = BytesIO()
            for chunk in response.iter_content(chunk_size=8192):
                buffer.write(chunk)
            buffer.seek(0)

            with ZipFile(buffer) as zf:
                yield zf


client = cast(SmartlingAPIClient, SimpleLazyObject(SmartlingAPIClient))
