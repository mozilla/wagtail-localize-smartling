import logging

from datetime import datetime
from functools import lru_cache
from typing import Iterable, Optional, Set

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.db import models
from django.db.models.manager import Manager
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from wagtail.admin.panels import FieldPanel
from wagtail_localize.components import register_translation_component
from wagtail_localize.models import Translation, TranslationSource
from wagtail_localize.tasks import ImmediateBackend, background

from . import utils
from .api.client import client
from .forms import JobForm


logger = logging.getLogger(__name__)


class SyncedModel(models.Model):
    first_synced_at = models.DateTimeField(null=True, editable=False)
    last_synced_at = models.DateTimeField(null=True, editable=False)

    class Meta:
        abstract = True


class Project(SyncedModel):
    """
    Represents a project in Smartling. There should normally only be one of
    these, it's synced from the Smartling API based on the PROJECT_ID setting.
    """

    account_uid = models.CharField(max_length=16)
    archived = models.BooleanField()
    project_id = models.CharField(max_length=16)
    name = models.CharField(max_length=255)
    type_code = models.CharField(max_length=16)
    source_locale_description = models.CharField(max_length=255)
    source_locale_id = models.CharField(max_length=16)

    target_locales: Manager["ProjectTargetLocale"]

    class Meta(SyncedModel.Meta):
        unique_together = ["account_uid", "project_id"]

    def __str__(self):
        return f"{self.name} ({self.project_id})"

    @classmethod
    @lru_cache
    def get_current(cls) -> "Project":
        """
        Returns the current Project as per the PROJECT_ID setting. The first
        time this is called, the project details are fetched from the Smartling
        API and a Project instance is created/updated as appropriate before
        being returned. Subsequent calls returned that instance from cache.
        """
        now = timezone.now()
        project_details = client.get_project_details()

        try:
            project = cls.objects.get(
                account_uid=project_details["accountUid"],
                project_id=project_details["projectId"],
            )
        except Project.DoesNotExist:
            project = cls(
                account_uid=project_details["accountUid"],
                project_id=project_details["projectId"],
                first_synced_at=now,
            )

        project.archived = project_details["archived"]
        project.name = project_details["projectName"]
        project.type_code = project_details["projectTypeCode"]
        project.source_locale_description = project_details["sourceLocaleDescription"]
        project.source_locale_id = project_details["sourceLocaleId"]

        project.last_synced_at = now
        project.save()

        seen_target_locale_ids: Set[str] = set()
        for target_locale_data in project_details["targetLocales"]:
            seen_target_locale_ids.add(target_locale_data["localeId"])
            ProjectTargetLocale.objects.update_or_create(
                project=project,
                locale_id=target_locale_data["localeId"],
                defaults={
                    "description": target_locale_data["description"],
                    "enabled": target_locale_data["enabled"],
                },
            )

        project.target_locales.exclude(locale_id__in=seen_target_locale_ids).delete()

        logger.info("Synced project %s", project)
        return project


class ProjectTargetLocale(models.Model):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="target_locales",
    )
    locale_id = models.CharField(max_length=16)
    description = models.CharField(max_length=255)
    enabled = models.BooleanField()

    class Meta:
        unique_together = ["project", "locale_id"]

    def __str__(self):
        return f"{self.description}"



@register_translation_component(
    # TODO better labels
    heading=_("Send translation to Smartling"),
    help_text=_("You can modify Smartling job details"),
    enable_text=_("Send to Smartling"),
    disable_text=_("Do not send to Smartling"),
)
class Job(SyncedModel):
    """
    Represents a job in Smartling and its links to TranslationSource and
    Translation objects in Wagtail.
    """

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="jobs")

    # wagtail-localize fields
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="+",
    )
    translation_source = models.ForeignKey(
        TranslationSource,
        on_delete=models.CASCADE,
        related_name="smartling_jobs",
    )
    translations = models.ManyToManyField(Translation, related_name="smartling_jobs")

    # Smartling job config fields
    name = models.CharField(max_length=170)
    description = models.TextField(blank=True)
    due_date = models.DateTimeField(blank=True, null=True)
    reference_number = models.CharField(max_length=255, blank=True, editable=False)

    # # Smartling API-derived fields
    # translation_job_uid = models.CharField(max_length=64, editable=False)

    base_form_class = JobForm
    panels = [
        FieldPanel("name"),
        FieldPanel("description"),
        FieldPanel("due_date"),
    ]

    def __str__(self):
        return self.name

    @classmethod
    def get_or_create_from_source_and_translation_data(
        cls,
        translation_source: TranslationSource,
        translations: Iterable[Translation],
        *,
        user: AbstractBaseUser,
        name: str,
        description: str,
        due_date: Optional[datetime],
    ) -> None:
        """
        This is the main entrypoint for creating Jobs. Jobs created here are in
        a pending state until the `sync_smartling` management command picks them
        up and creates a corresponding job in Smartling via the API.

        Jobs are only created if there's no pending or completed job for the provided
        TranslationSource.
        """
        job = Job.objects.create(
            project=Project.get_current(),
            translation_source=translation_source,
            user=user,
            name=name,
            description=description,
            due_date=due_date,
            reference_number=f"translationsource_id:{translation_source.pk}",
        )
        job.translations.set(translations)

        if isinstance(background, ImmediateBackend):
            # Don't enqueue anything slow if we're using the dummy background
            # worker, let the `sync_smartling` management command pick things up
            # on a schedule instead
            return

        # TODO if we get here we've got a proper background worker, so enqueue
        # the API-related stuff
