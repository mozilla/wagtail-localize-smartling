import pytest

from rest_framework.exceptions import ValidationError

from wagtail_localize_smartling.api.serializers import (
    UploadFileToBatchResponseSerializer,
)


pytestmark = [pytest.mark.django_db]


def test_UploadFileToBatchResponseSerializer__valid():
    valid_response = {
        "response": {
            "code": "ACCEPTED",
            "data": None,  # Note: the regular ResponseSerializer would not like this None
        }
    }
    serializer = UploadFileToBatchResponseSerializer(data=valid_response)
    assert serializer.is_valid(), serializer.errors
    validated_data = serializer.validated_data
    assert validated_data["response"]["code"] == "ACCEPTED"
    assert validated_data["response"]["data"] is None


def test_UploadFileToBatchResponseSerializer__invalid_code():
    invalid_response = {
        "response": {
            "code": "REJECTED",
            "data": None,
        }
    }
    serializer = UploadFileToBatchResponseSerializer(data=invalid_response)
    with pytest.raises(ValidationError):
        serializer.is_valid(raise_exception=True)


def test_UploadFileToBatchResponseSerializer__data_present():
    valid_response = {
        "response": {
            "code": "ACCEPTED",
            "data": {
                "file_id": "12345"
            },  # Unrealistic to get a value back for `data`, but possible if the API changed behaviour
        }
    }
    serializer = UploadFileToBatchResponseSerializer(data=valid_response)
    assert serializer.is_valid(), serializer.errors
    validated_data = serializer.validated_data
    assert validated_data["response"]["code"] == "ACCEPTED"
    assert validated_data["response"]["data"] == {}  # cleaned up to {} but still falsey


def test_UploadFileToBatchResponseSerializer__data_and_errors():
    invalid_response = {
        "response": {
            "code": "ACCEPTED",
            "data": {"file_id": "12345"},
            "errors": [{"key": "ERROR_KEY", "message": "Some error"}],
        }
    }
    serializer = UploadFileToBatchResponseSerializer(data=invalid_response)
    with pytest.raises(ValidationError):
        serializer.is_valid(raise_exception=True)
