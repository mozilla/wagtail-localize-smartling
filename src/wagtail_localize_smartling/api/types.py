from datetime import datetime
from typing import Any, List, Literal, Optional, TypedDict

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
    targetLocales: List[TargetLocaleData]


class ListJobsItem(TypedDict):
    jobName: str


class ListJobsResponseData(TypedDict):
    items: List[ListJobsItem]


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
    callbackMethod: Optional[str]
    callbackUrl: Optional[str]
    createdByUserUid: str
    createdDate: datetime
    description: str
    dueDate: Optional[datetime]
    firstCompletedDate: Optional[datetime]
    firstAuthorizedDate: Optional[datetime]
    jobName: str
    jobNumber: Optional[str]
    jobStatus: JobStatus
    lastCompletedDate: Optional[datetime]
    lastAuthorizedDate: Optional[datetime]
    modifiedByUserUid: Optional[str]
    modifiedDate: datetime
    targetLocaleIds: List[str]
    translationJobUid: str
    referenceNumber: Optional[str]
    issues: Optional[IssuesCountData]


class SourceFileData(TypedDict):
    name: str
    uri: str
    fileUid: str


class GetJobDetailsResponseData(CreateJobResponseData):
    priority: Optional[int]
    sourceFiles: List[SourceFileData]
