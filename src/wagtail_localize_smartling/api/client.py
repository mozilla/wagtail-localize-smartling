import logging
import pprint
import textwrap

from datetime import datetime, timedelta
from functools import cached_property
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    List,
    Literal,
    Optional,
    Type,
    TypeVar,
    cast,
)
from urllib.parse import quote, urljoin

import requests
import requests.exceptions
import rest_framework.serializers

from django.utils import timezone
from django.utils.functional import SimpleLazyObject
from polib import POFile
from requests.exceptions import HTTPError

from ..settings import settings
from . import types
from .serializers import (
    AddFileToJobResponseSerializer,
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

    def __init__(self, *, code: str, errors: List[types.SmartlingAPIErrorDict]):
        self.code = code
        self.errors = errors

    def __str__(self):
        return (
            f"{self.code}\n"
            f"{textwrap.indent(pprint.pformat(self.errors, indent=2), prefix='  ')}"
        )


# API client

# TODO allow customization of serializerrs to account for custom fields

RD = TypeVar("RD", bound=dict)


class SmartlingAPIClient:
    def __init__(self):
        self.token_type: str = "Bearer"
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None

        # Set expiry times in the past, initially
        a_day_ago = timezone.now() - timedelta(days=1)
        self.access_token_expires_at: datetime = a_day_ago
        self.refresh_token_expires_at: datetime = a_day_ago

    # Utilities

    @property
    def _headers(self) -> Dict[str, str]:
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
                    "userIdentifier": settings.USER_IDENTIFIER,
                    "userSecret": settings.USER_SECRET,
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
        if settings.ENVIRONMENT == "production":
            return "https://api.smartling.com"
        elif settings.ENVIRONMENT == "staging":
            return "https://api.stg.smartling.net"
        raise SmartlingAPIError(f"Unknown environment: {settings.ENVIRONMENT}")

    def _request(
        self,
        *,
        method: Literal["GET", "POST"],
        path: str,
        response_serializer_class: Type[ResponseSerializer],
        send_headers: bool = True,
        **kwargs,
    ) -> Dict[str, Any]:
        if method == "GET":
            requests_method = requests.get
        elif method == "POST":
            requests_method = requests.post
        else:
            raise RuntimeError(f"Invalid request method: {method}")

        url = urljoin(self._base_url, path)
        headers = self._headers if send_headers else {}

        logger.info(
            "Smartling API request: %s %s",
            method,
            url,
        )
        response = requests_method(
            url, headers=headers, timeout=settings.API_TIMEOUT_SECONDS, **kwargs
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
            # This sort of cast is required because the created instance could
            # be a ListSerializer if we passed many=True, but we know better.
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
        self,
        *,
        include_disabled_locales: bool = True,
    ) -> types.GetProjectDetailsResponseData:
        params = {}
        if include_disabled_locales:
            params["includeDisabledLocales"] = "true"
        return cast(
            types.GetProjectDetailsResponseData,
            self._request(
                method="GET",
                path=f"/projects-api/v2/projects/{quote(settings.PROJECT_ID)}",
                response_serializer_class=GetProjectDetailsResponseSerializer,
                params=params,
            ),
        )

    def list_jobs(self, *, name: Optional[str] = None) -> types.ListJobsResponseData:
        params = {}
        if name is not None:
            params["jobName"] = name
        return cast(
            types.ListJobsResponseData,
            self._request(
                method="GET",
                path=f"/jobs-api/v3/projects/{quote(settings.PROJECT_ID)}/jobs",
                response_serializer_class=ListJobsResponseSerializer,
                params=params,
            ),
        )

    def create_job(
        self,
        *,
        job_name: str,
        target_locale_ids: Optional[List[str]] = None,
        description: Optional[str] = None,
        due_date: Optional[datetime] = None,
        reference_number: Optional[str] = None,
        callback_url: Optional[str] = None,
        callback_method: Optional[Literal["GET", "POST"]] = None,
    ) -> Dict[str, Any]:
        if (callback_url is None) != (callback_method is None):
            raise ValueError(
                "Both callback_url and callback_method must be provided, or neither"
            )

        params: Dict[str, Any] = {
            "jobName": job_name,
        }
        if target_locale_ids is not None:
            params["targetLocaleIds"] = target_locale_ids
        if description is not None:
            params["description"] = description
        if due_date is not None:
            params["dueDate"] = due_date.isoformat()
        if reference_number is not None:
            params["referenceNumber"] = reference_number
        if callback_url is not None:
            params["callbackMethod"] = callback_method
            params["callbackUrl"] = callback_url

        return self._request(
            method="POST",
            path=f"/jobs-api/v3/projects/{quote(settings.PROJECT_ID)}/jobs",
            response_serializer_class=CreateJobResponseSerializer,
            json=params,
        )

    def get_job_details(
        self,
        *,
        job: "Job",
    ) -> Dict[str, Any]:
        return self._request(
            method="GET",
            path=f"/jobs-api/v3/projects/{quote(settings.PROJECT_ID)}/jobs/{quote(job.translation_job_uid)}",
            response_serializer_class=GetJobDetailsResponseSerializer,
        )

    def upload_po_file(
        self,
        *,
        po_file: POFile,
        file_name: str,
        file_uri: str,
        namespace: Optional[str] = None,
    ):
        # TODO handle 202 reponses for files that take over a minute to upload

        return self._request(
            method="POST",
            path=f"/files-api/v2/projects/{quote(settings.PROJECT_ID)}/file",
            response_serializer_class=UploadFileResponseSerializer,
            files={
                "file": (file_name, str(po_file)),
            },
            data={
                "fileUri": file_uri,
                "fileType": "gettext",
            },
        )

    def add_file_to_job(
        self,
        *,
        translation_job_uid: str,
        file_uri: str,
        target_locale_ids: Optional[List[str]] = None,
    ):
        # TODO handle 202 responses for files that get added asynchronously

        body: Dict[str, Any] = {
            "fileUri": file_uri,
        }
        if target_locale_ids is not None:
            body["targetLocaleIds"] = target_locale_ids

        return self._request(
            method="POST",
            path=f"/jobs-api/v3/projects/{quote(settings.PROJECT_ID)}/jobs/{quote(translation_job_uid)}/file/add",
            response_serializer_class=AddFileToJobResponseSerializer,
            json=body,
        )


client = cast(SmartlingAPIClient, SimpleLazyObject(SmartlingAPIClient))
