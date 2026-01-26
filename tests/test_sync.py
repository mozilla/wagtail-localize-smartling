"""Tests for the sync module, including per-locale import (Issue #37)."""

from unittest.mock import patch

import pytest

from django.utils import timezone

from wagtail_localize_smartling.models import Job, JobTranslation
from wagtail_localize_smartling.sync import (
    _check_and_import_completed_locales,
    _import_translation_for_locale,
)


pytestmark = pytest.mark.django_db


@pytest.fixture
def disable_signals():
    """Disable translation import signals during tests."""
    with patch("wagtail_localize_smartling.sync.individual_translation_imported") as mock_signal:
        mock_signal.send = lambda *args, **kwargs: None
        yield mock_signal


class TestCheckAndImportCompletedLocales:
    """Tests for _check_and_import_completed_locales function (Issue #37)."""

    def test_imports_completed_locale(
        self,
        smartling_job_multi_locale: Job,
        smartling_get_file_status,
        smartling_download_translation_for_locale,
        disable_signals,
    ):
        """Test that a completed locale is imported while job is in progress."""
        # Set up: fr is complete, de is not
        smartling_get_file_status("fr", total_strings=10, completed_strings=10)
        smartling_get_file_status("de", total_strings=10, completed_strings=5)
        smartling_download_translation_for_locale("fr")

        # Verify job_translations exist and are not imported
        job_translations = JobTranslation.objects.filter(job=smartling_job_multi_locale)
        assert job_translations.count() == 2
        assert all(jt.imported_at is None for jt in job_translations)

        # Run the import
        imported = _check_and_import_completed_locales(smartling_job_multi_locale)

        # Should have imported fr but not de
        assert len(imported) == 1
        assert imported[0].target_locale.language_code == "fr"

        # Check JobTranslation records
        fr_jt = job_translations.get(translation__target_locale__language_code="fr")
        de_jt = job_translations.get(translation__target_locale__language_code="de")

        assert fr_jt.imported_at is not None
        assert de_jt.imported_at is None

    def test_no_import_when_not_complete(
        self,
        smartling_job_multi_locale: Job,
        smartling_get_file_status,
    ):
        """Test that incomplete locales are not imported."""
        # Both locales incomplete
        smartling_get_file_status("fr", total_strings=10, completed_strings=5)
        smartling_get_file_status("de", total_strings=10, completed_strings=3)

        imported = _check_and_import_completed_locales(smartling_job_multi_locale)

        assert len(imported) == 0

        # No JobTranslations should be marked as imported
        job_translations = JobTranslation.objects.filter(job=smartling_job_multi_locale)
        assert all(jt.imported_at is None for jt in job_translations)

    def test_skips_already_imported(
        self,
        smartling_job_multi_locale: Job,
        smartling_get_file_status,
    ):
        """Test that already imported locales are skipped."""
        # Mark fr as already imported
        fr_jt = JobTranslation.objects.get(
            job=smartling_job_multi_locale,
            translation__target_locale__language_code="fr",
        )
        fr_jt.imported_at = timezone.now()
        fr_jt.save()

        # Only de should be checked (and it's incomplete)
        smartling_get_file_status("de", total_strings=10, completed_strings=5)

        imported = _check_and_import_completed_locales(smartling_job_multi_locale)

        assert len(imported) == 0

    def test_imports_all_completed_locales(
        self,
        smartling_job_multi_locale: Job,
        smartling_get_file_status,
        smartling_download_translation_for_locale,
        disable_signals,
    ):
        """Test that all completed locales are imported."""
        # Both locales complete
        smartling_get_file_status("fr", total_strings=10, completed_strings=10)
        smartling_get_file_status("de", total_strings=10, completed_strings=10)
        smartling_download_translation_for_locale("fr")
        smartling_download_translation_for_locale("de")

        imported = _check_and_import_completed_locales(smartling_job_multi_locale)

        assert len(imported) == 2

        # Both should be marked as imported
        job_translations = JobTranslation.objects.filter(job=smartling_job_multi_locale)
        assert all(jt.imported_at is not None for jt in job_translations)

    def test_handles_api_error_gracefully(
        self,
        smartling_job_multi_locale: Job,
        smartling_get_file_status,
        smartling_download_translation_for_locale,
        responses,
        disable_signals,
    ):
        """Test that API errors for one locale don't prevent others from importing."""
        # fr will succeed, de will fail
        smartling_get_file_status("fr", total_strings=10, completed_strings=10)
        smartling_download_translation_for_locale("fr")
        # de request not mocked, so it will fail

        # Should still import fr
        imported = _check_and_import_completed_locales(smartling_job_multi_locale)

        assert len(imported) == 1
        assert imported[0].target_locale.language_code == "fr"


class TestImportTranslationForLocale:
    """Tests for _import_translation_for_locale function."""

    def test_imports_single_locale(
        self,
        smartling_job: Job,
        smartling_download_translation_for_locale,
        disable_signals,
    ):
        """Test importing a single locale's translation."""
        smartling_download_translation_for_locale("fr")

        translation = smartling_job.translations.first()
        _import_translation_for_locale(smartling_job, translation, "fr")

        # The translation should have been imported (import_po was called)
        # We can verify by checking that no exception was raised
