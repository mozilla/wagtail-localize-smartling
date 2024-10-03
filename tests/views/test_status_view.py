# Functionality TODO:
# - Target locale compatibility

# Cases TODO:
# - Auth error (no project)
# - Incorrect project ID (no project)
# - Suggested source locale is synced from another locale
# - Suggested source locale does not exist
# - No suggested source locale

import pytest

from bs4 import BeautifulSoup
from django.urls import reverse


@pytest.mark.django_db()
def test_everything_working(client, superuser, smartling_project):
    client.force_login(superuser)

    url = reverse("wagtail_localize_smartling:status")
    response = client.get(url)

    assert response.status_code == 200

    soup = BeautifulSoup(response.content.decode(), "html.parser")
    main = soup.find("main")
    assert main is not None
    text = main.get_text(separator=" ", strip=True)

    # Negative tests for error messages
    assert "Could not load the Smartling project" not in text
    assert "The source locale is not compatible" not in text

    # Project metadata
    assert f"Project ID {smartling_project.project_id}" in text
    assert f"Project name {smartling_project.name}" in text
    assert f"Source locale {smartling_project.source_locale_description}" in text

    # Source locale
    assert "The source locale is compatible with Smartling" in text

    # TODO Target locales
