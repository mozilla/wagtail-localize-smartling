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
from ..settings import settings as smartling_settings
from . import types
from .serializers import (
    AddFileToJobResponseSerializer,
    AddVisualContextToJobSerializer,
    AuthenticateResponseSerializer,
    CreateJobResponseSerializer,
    GetJobDetailsResponseSerializer,
    GetProjectDetailsResponseSerializer,
    ListJobsResponseSerializer,
    RefreshAccessTokenResponseSerializer,
    ResponseSerializer,
    UploadFileResponseSerializer,
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
        response_serializer_class: type[ResponseSerializer],
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
            response_serializer_class,
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

    def upload_po_file_for_job(self, *, job: "Job") -> str:
        # TODO handle 202 reponses for files that take over a minute to upload

        # TODO document why we're using the Job PK for the file URI. It's
        # because Smartling uses this as the default namespace for any
        # strings contained in the file, and strings can only exist once in
        # a namespace. If we were to upload the same file to multiple jobs
        # (e.g. if a page got translated, converted back to an alias and
        # then translated again), then the second job would contain no
        # strings because they're all part of the first job. You can't
        # authorize a job with no strings, so it'd be effectively stuck.
        # TODO make this more unique, including page identifier
        file_uri = f"job_{job.pk}.po"
        self._request(
            method="POST",
            path=f"/files-api/v2/projects/{quote(job.project.project_id)}/file",
            response_serializer_class=UploadFileResponseSerializer,
            files={
                "file": (file_uri, str(job.translation_source.export_po())),
            },
            data={
                "fileUri": file_uri,
                "fileType": "gettext",
            },
        )
        return file_uri

    def add_file_to_job(self, *, job: "Job"):
        # TODO handle 202 responses for files that get added asynchronously

        body: dict[str, Any] = {
            "fileUri": job.file_uri,
            "targetLocaleIds": [
                utils.format_smartling_locale_id(t.target_locale.language_code)
                for t in job.translations.all()
            ],
        }

        return self._request(
            method="POST",
            path=f"/jobs-api/v3/projects/{quote(job.project.project_id)}/jobs/{quote(job.translation_job_uid)}/file/add",
            response_serializer_class=AddFileToJobResponseSerializer,
            json=body,
        )

    def add_html_context_to_job(self, *, job: "Job"):
        if visual_context_callback_fn := smartling_settings.VISUAL_CONTEXT_CALLBACK:
            url, html = visual_context_callback_fn(job)

            if isinstance(html, str):
                html = bytearray(html, "utf-8")

            # We need to make a multipart/form-data payload containing
            # `name` - url of the page the Job is for
            # `content` - the HTML of the relevant Page for this Job, as bytes
            # `matchparams` - config params for Smartling's string matching

            data_payload: dict[str, Any] = {
                "name": url,
                "matchparams": {
                    "translationJobUids": [job.translation_job_uid],
                },
            }

            file_payload: dict[tuple[str, str]] = {
                "content": (url.split("/")[-1], html, "text/html"),
            }

            logger.info(
                "Sending visual context to Smartling for Job %s/%s for URL %s",
                job.id,
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
