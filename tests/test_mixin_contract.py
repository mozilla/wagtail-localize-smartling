import pytest

from django.db import models

from wagtail_localize_smartling.locks import PendingTranslationLockMixin


@pytest.fixture(autouse=True)
def locales():
    """Shadow the conftest fixture, which would otherwise hit the database."""
    return []


def test_the_mixin_is_not_a_model():
    """
    Projects should add the mixin into an existing page model, so the mixin must
    not add fields (which require a migration).

    A failure here is loud, like
    FAILED tests/test_mixin_contract.py::test_the_mixin_is_not_a_model
    and shows up as a failure, above any errors on tests.
    """
    assert not issubclass(PendingTranslationLockMixin, models.Model)
