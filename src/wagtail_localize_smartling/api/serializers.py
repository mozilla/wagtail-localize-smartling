from datetime import UTC
from functools import cached_property
from typing import Any, ClassVar

from rest_framework import serializers

from . import types


# TODO document the reasons for using serializers like this and the approach, i.e.:
# - DRF is already a dependency of Wagtail and it's good at validating JSON, so
#   that's why we're using it in the first instance
# - The serializers here encode our assumptions about the expected structure of
#   API _responses_ (we don't both using serializers for request bodies, they're
#   much simpler)
# - It wculd be nice
# (if somewhat verbose) to define TypedDict subclasses for all of the response
# types and get rid of the string-based item access in the rest of the code, but
# that's a nice-to-have if we have time later on


###################
# Utility classes #
###################


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

    def __new__(cls, name, bases, attrs, **kwargs):
        if any(isinstance(b, ResponseSerializerMetaclass) for b in bases):
            # Identify whether this is a subclass requiring nullable `data`

            # This a ResponseSerializer subclass, so loop over attrs and strip
            # off any fields and nested serializers
            data_serializer_fields: dict[str, serializers.Field] = {}
            for attr_name, attr in list(attrs.items()):
                if isinstance(attr, serializers.Field):
                    data_serializer_fields[attr_name] = attrs.pop(attr_name)

            # Determine the base classes for a new data serializer class
            data_serializer_bases = tuple(
                b._data_serializer_class
                for b in bases
                if hasattr(b, "_data_serializer_class")
            )
            if not data_serializer_bases:
                data_serializer_bases = (serializers.Serializer,)

            # Create the new data serializer class with the fields we stripped off above
            data_serializer_class = type(
                f"{name}__DataSerializer",
                data_serializer_bases,
                data_serializer_fields,
            )

            # Create a new InnerResponseSerializer subclass whose "data" field
            # is an instance the new data serializer subclass that we just created
            inner_response_serializer_class = type(
                f"{name}__InnerResponseSerializer",
                (InnerResponseSerializer,),
                {"data": data_serializer_class(required=False)},
            )

            # Finally, create and return the new ResponseSerializer subclass
            # with its "response" field declared as an instance of the
            # InnerResponseSerializer subclass we just created. Also attach a
            # reference to the data serializer class we just created so we can
            # use it when subclassing subclasses.
            return super().__new__(
                cls,
                name,
                bases,
                {
                    **attrs,
                    "response": inner_response_serializer_class(),
                    "_data_serializer_class": data_serializer_class,
                },
            )

        return super().__new__(cls, name, bases, attrs)


class ErrorSerializer(serializers.Serializer):
    key = serializers.CharField(allow_blank=True, allow_null=True)
    message = serializers.CharField(allow_blank=True, allow_null=True)
    details = serializers.JSONField(required=False)


class InnerResponseSerializer(serializers.Serializer):
    code = serializers.CharField()
    # data = ...__DataSerializer() <- Created by ResponseSerializerMetaclass

    # This looks like it would hide the serializer.errors property, but thanks
    # to DRF's SerializerMetaclass magic, that's not the case. It does give
    # Pyright a headache though.
    errors = ErrorSerializer(required=False, many=True)  # pyright: ignore[reportIncompatibleMethodOverride, reportAssignmentType]

    def validate(self, attrs: dict[str, Any]):
        if "data" in attrs and "errors" in attrs:
            raise serializers.ValidationError("data and errors cannot both be present")
        return attrs


class ResponseSerializer(
    serializers.Serializer,
    metaclass=ResponseSerializerMetaclass,
):
    # response = ...__InnerResponseSerializer() <- Created by ResponseSerializerMetaclass  # noqa: E501

    _data_serializer_class: ClassVar[type[serializers.Serializer]]

    @cached_property
    def response_data(self) -> dict[str, Any]:
        return self.validated_data["response"]["data"]

    @cached_property
    def response_errors(self) -> tuple[str, list[types.SmartlingAPIErrorDict]]:
        return (
            self.validated_data["response"]["code"],
            self.validated_data["response"]["errors"],
        )


class NullDataResponseSerializer(ResponseSerializer):
    _acceptable_codes_for_null_response = [
        # Subclasses should define what are acceptable response messages
        # based on the use-case. e.g.
        # "ACCEPTED",
    ]

    def validate_empty_values(self, data) -> tuple[bool, Any]:
        """
        Overrides the default behavior to allow `None` for the `data` field.
        """
        if "response" in data and (
            data["response"]["data"] is None
            and hasattr(self, "_acceptable_codes_for_null_response")
            and data["response"]["code"] in self._acceptable_codes_for_null_response
        ):
            # Consider this as valid for the serializer
            return True, data
        return super().validate_empty_values(data)


###################################################
# Response serializers for specific API endpoints #
###################################################

# TODO trim these down to just the fields we actually use so that we're not
#      being unnecessarily fussy


class AuthenticateResponseSerializer(
    ResponseSerializer,
    response_data_class=types.AuthenticateResponseData,
):
    # https://api-reference.smartling.com/#tag/Authentication/operation/authenticate
    accessToken = serializers.CharField()
    expiresIn = serializers.IntegerField()
    refreshExpiresIn = serializers.IntegerField()
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


class ListJobsItemSerializer(serializers.Serializer):
    jobName = serializers.CharField()


class ListJobsResponseSerializer(ResponseSerializer):
    items = ListJobsItemSerializer(many=True)


class IssuesCountSerializer(serializers.Serializer):
    sourceIssuesCount = serializers.IntegerField()
    translationIssuesCount = serializers.IntegerField()


class CreateJobResponseSerializer(ResponseSerializer):
    # https://api-reference.smartling.com/#tag/Jobs/operation/addJob
    callbackMethod = serializers.ChoiceField(choices=["GET", "POST"], allow_null=True)
    callbackUrl = serializers.URLField(allow_null=True)
    createdByUserUid = serializers.CharField()
    createdDate = serializers.DateTimeField(default_timezone=UTC)
    description = serializers.CharField(allow_blank=True)
    dueDate = serializers.DateTimeField(default_timezone=UTC, allow_null=True)
    firstCompletedDate = serializers.DateTimeField(
        default_timezone=UTC,
        allow_null=True,
    )
    firstAuthorizedDate = serializers.DateTimeField(
        default_timezone=UTC,
        allow_null=True,
    )
    jobName = serializers.CharField()
    jobNumber = serializers.CharField(allow_null=True)
    jobStatus = serializers.ChoiceField(choices=types.JobStatus.values)
    lastCompletedDate = serializers.DateTimeField(
        default_timezone=UTC,
        allow_null=True,
    )
    lastAuthorizedDate = serializers.DateTimeField(
        default_timezone=UTC,
        allow_null=True,
    )
    modifiedByUserUid = serializers.CharField(allow_null=True)
    modifiedDate = serializers.DateTimeField(default_timezone=UTC)
    targetLocaleIds = serializers.ListField(child=serializers.CharField())
    translationJobUid = serializers.CharField()
    referenceNumber = serializers.CharField(allow_null=True, allow_blank=True)
    issues = IssuesCountSerializer(allow_null=True)


class SourceFileSerializer(serializers.Serializer):
    name = serializers.CharField()
    uri = serializers.CharField()
    fileUid = serializers.CharField()


class GetJobDetailsResponseSerializer(CreateJobResponseSerializer):
    # https://api-reference.smartling.com/#tag/Jobs/operation/getJobDetails
    priority = serializers.IntegerField(allow_null=True)
    sourceFiles = serializers.ListField(child=SourceFileSerializer())


class AddVisualContextToJobSerializer(ResponseSerializer):
    # https://api-reference.smartling.com/#tag/Context/operation/uploadAndMatchVisualContext
    processUid = serializers.CharField()


class CreateBatchResponseSerializer(ResponseSerializer):
    # https://api-reference.smartling.com/#tag/Job-Batches-V2/operation/createJobBatchV2
    batchUid = serializers.CharField()


class UploadFileToBatchResponseSerializer(NullDataResponseSerializer):
    # https://api-reference.smartling.com/#tag/Job-Batches-V2/operation/uploadFileToJobBatchV2
    _acceptable_codes_for_null_response = [
        "ACCEPTED",
    ]
