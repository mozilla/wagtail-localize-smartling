from typing import Any, TypedDict


class _SmartlingAPIErrorDictBase(TypedDict):
    key: str
    message: str


class SmartlingAPIErrorDict(_SmartlingAPIErrorDictBase, total=False):
    details: Any
