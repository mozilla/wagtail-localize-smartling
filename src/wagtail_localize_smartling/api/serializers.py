from functools import cached_property
from typing import Any, Dict, List, Tuple

from rest_framework import serializers

from .types import SmartlingAPIErrorDict


class ResponseSerializerMetaclass(serializers.SerializerMetaclass):
    """
    Custom Serializer metaclass for ResponseSerializer that makes declaring
    fields for Smartling API response serializers DRYer.

    Smartling JSON responses all look like this:
    {
        "response": {
            "code": ...
            "data": ...    <- Only present for successful requests
            "errors": ...  <- Only present for failed requests
        }
    }

    The "code" and "errors" fields share a common format across all responses,
    so those get declared once on InnerResponseSerializer. Any fields declared
    on ResponseSerializer subclasses are moved to a nested "response"."data"
    serializer and the data can be accessed via the `.response_data` property.
    """

    def __new__(cls, name, bases, attrs):
        if name != "ResponseSerializer" and ResponseSerializer in bases:
            # This a ResponseSerializer subclass, so loop over attrs and strip
            # off any fields and nested serializers
            data_serializer_fields: Dict[str, serializers.Field] = {}
            for attr_name, attr in list(attrs.items()):
                if isinstance(attr, serializers.Field):
                    data_serializer_fields[attr_name] = attrs.pop(attr_name)

            # Create a new Serializer subclass with the fields we stripped off above
            data_serializer_cls = type(
                f"{name}__DataSerializer",
                (serializers.Serializer,),
                data_serializer_fields,
            )

            # Create a new InnerResponseSerializer subclass whose "data" field
            # is an instance the new data serializer subclass that we just created
            inner_response_serializer_cls = type(
                f"{name}__InnerResponseSerializer",
                (InnerResponseSerializer,),
                {"data": data_serializer_cls(required=False)},
            )

            # Finally, create and return the new ResponseSerializer subclass
            # with its "response" field declared as an instance of the
            # InnerResponseSerializer subclass we just created
            return super().__new__(
                cls,
                name,
                bases,
                {**attrs, "response": inner_response_serializer_cls()},
            )

        return super().__new__(cls, name, bases, attrs)


class ErrorSerializer(serializers.Serializer):
    key = serializers.CharField()
    message = serializers.CharField()
    details = serializers.CharField(
        required=False,
        allow_blank="",
        trim_whitespace=False,
    )


class InnerResponseSerializer(serializers.Serializer):
    code = serializers.CharField()
    # data = ...__DataSerializer() <- Created by ResponseSerializerMetaclass
    response_errors = ErrorSerializer(required=False, many=True, source="errors")

    def validate(self, attrs: Dict[str, Any]):
        if "data" in attrs and "errors" in attrs:
            raise serializers.ValidationError("data and errors cannot both be present")
        return attrs


class ResponseSerializer(serializers.Serializer, metaclass=ResponseSerializerMetaclass):
    # response = ...__InnerResponseSerializer() <- Created by ResponseSerializerMetaclass  # noqa: E501

    @cached_property
    def response_data(self) -> Dict[str, Any]:
        return self.validated_data["response"].get("data")  # pyright: ignore[reportOptionalSubscript, reportIndexIssue]

    @cached_property
    def response_errors(self) -> Tuple[str, List[SmartlingAPIErrorDict]]:
        return (
            self.validated_data["response"]["code"],  # pyright: ignore[reportOptionalSubscript, reportIndexIssue]
            self.validated_data["response"].get("errors", []),  # pyright: ignore[reportOptionalSubscript, reportIndexIssue]
        )


class AuthenticateResponseSerializer(ResponseSerializer):
    # https://api-reference.smartling.com/#tag/Authentication/operation/authenticate
    accessToken = serializers.CharField()
    expiresIn = serializers.IntegerField(required=False, default=0)
    refreshExpiresIn = serializers.IntegerField(required=False, default=0)
    refreshToken = serializers.CharField()
    tokenType = serializers.ChoiceField(choices=["Bearer"], allow_blank=False)


class RefreshAccessTokenResponseSerializer(AuthenticateResponseSerializer):
    # https://api-reference.smartling.com/#tag/Authentication/operation/refreshAccessToken
    pass


class TargetLocaleSerializer(serializers.Serializer):
    description = serializers.CharField()
    enabled = serializers.BooleanField()
    localeId = serializers.CharField()


class GetProjectDetailsResponseSerializer(ResponseSerializer):
    # https://api-reference.smartling.com/#tag/Account-and-Projects/operation/getProjectDetails
    accountUid = serializers.CharField()
    archived = serializers.BooleanField()
    projectId = serializers.CharField()
    projectName = serializers.CharField()
    projectTypeCode = serializers.CharField()
    sourceLocaleDescription = serializers.CharField()
    sourceLocaleId = serializers.CharField()
    targetLocales = TargetLocaleSerializer(many=True)
