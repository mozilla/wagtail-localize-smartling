from typing import Any, List, Literal, TypedDict


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
