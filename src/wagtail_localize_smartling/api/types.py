from datetime import datetime
from typing import Any, Literal, TypedDict

from django.db import models
from django.utils.translation import gettext_lazy as _


class _SmartlingAPIErrorDictBase(TypedDict):
    key: str
    message: str


class SmartlingAPIErrorDict(_SmartlingAPIErrorDictBase, total=False):
    details: Any


class AuthenticateResponseData(TypedDict):
    accessToken: str
    expiresIn: int
    refreshExpiresIn: int
    refreshToken: str
    tokenType: Literal["Bearer"]


class RefreshAccessTokenResponseData(AuthenticateResponseData):
    pass


class TargetLocaleData(TypedDict):
    description: str
    enabled: bool
    localeId: str


class GetProjectDetailsResponseData(TypedDict):
    accountUid: str
    archived: bool
    projectId: str
    projectName: str
    projectTypeCode: str
    sourceLocaleDescription: str
    sourceLocaleId: str
    targetLocales: list[TargetLocaleData]


class ListJobsItem(TypedDict):
    jobName: str


class ListJobsResponseData(TypedDict):
    items: list[ListJobsItem]


class JobStatus(models.TextChoices):
    UNSYNCED = ("UNSYNCED", _("Unsynced"))
    DRAFT = ("DRAFT", _("Draft"))
    AWAITING_AUTHORIZATION = ("AWAITING_AUTHORIZATION", _("Awaiting authorization"))
    IN_PROGRESS = ("IN_PROGRESS", _("In progress"))
    COMPLETED = ("COMPLETED", _("Completed"))
    CANCELLED = ("CANCELLED", _("Cancelled"))
    CLOSED = ("CLOSED", _("Closed"))
    DELETED = ("DELETED", _("Deleted"))


class UploadFileResponseData(TypedDict):
    overWritten: bool
    stringCount: int
    wordCount: int


class IssuesCountData(TypedDict):
    sourceIssuesCount: int
    translationIssuesCount: int


class CreateJobResponseData(TypedDict):
    callbackMethod: str | None
    callbackUrl: str | None
    createdByUserUid: str
    createdDate: datetime
    description: str
    dueDate: datetime | None
    firstCompletedDate: datetime | None
    firstAuthorizedDate: datetime | None
    jobName: str
    jobNumber: str | None
    jobStatus: JobStatus
    lastCompletedDate: datetime | None
    lastAuthorizedDate: datetime | None
    modifiedByUserUid: str | None
    modifiedDate: datetime
    targetLocaleIds: list[str]
    translationJobUid: str
    referenceNumber: str | None
    issues: IssuesCountData | None


class SourceFileData(TypedDict):
    name: str
    uri: str
    fileUid: str


class GetJobDetailsResponseData(CreateJobResponseData):
    priority: int | None
    sourceFiles: list[SourceFileData]


class AddVisualContextToJobResponseData(TypedDict):
    processUid: str


class CreateBatchForJobResponseData(TypedDict):
    batchUid: str


class UploadFileToBatchResponseData(TypedDict):
    pass
