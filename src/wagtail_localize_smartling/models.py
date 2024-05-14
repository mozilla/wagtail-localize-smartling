import logging

from typing import Set

from django.db import models, transaction
from django.db.models.manager import Manager
from django.utils import timezone

from .api.client import client


logger = logging.getLogger(__name__)


class SyncedModel(models.Model):
    first_synced_at = models.DateTimeField()
    last_synced_at = models.DateTimeField()

    class Meta:
        abstract = True


class Project(SyncedModel):
    account_uid = models.CharField(max_length=16)
    archived = models.BooleanField()
    project_id = models.CharField(max_length=16)
    name = models.CharField(max_length=255)
    type_code = models.CharField(max_length=16)
    source_locale_description = models.CharField(max_length=255)
    source_locale_id = models.CharField(max_length=16)

    target_locales: Manager["TargetLocale"]

    class Meta(SyncedModel.Meta):
        unique_together = ["account_uid", "project_id"]

    def __str__(self):
        return f"{self.name} ({self.project_id})"

    @classmethod
    @transaction.atomic
    def sync(cls):
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
                first_synced_at=timezone.now(),
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
            TargetLocale.objects.update_or_create(
                project=project,
                locale_id=target_locale_data["localeId"],
                defaults={
                    "description": target_locale_data["description"],
                    "enabled": target_locale_data["enabled"],
                },
            )

        project.target_locales.exclude(locale_id__in=seen_target_locale_ids).delete()

        logger.info("Synced project %s", project)


class TargetLocale(models.Model):
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


# class Job(models.Model):
#     project = models.ForeignKey(Project, on_delete=models.CASCADE)
#     name = models.CharField(max_length=170)


# class JobItem(models.Model):
#     pass
