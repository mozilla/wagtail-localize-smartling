import hashlib
import logging

from collections.abc import Iterable
from datetime import datetime
from functools import lru_cache

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models.manager import Manager
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from wagtail.admin.panels import FieldPanel
from wagtail.models import Locale
from wagtail_localize.components import register_translation_component
from wagtail_localize.models import Translation, TranslationSource
from wagtail_localize.tasks import ImmediateBackend, background

from .api.client import client
from .api.types import JobStatus
from .constants import UNSYNCED_OR_PENDING_STATUSES
from .forms import JobForm
from .settings import settings as smartling_settings
from .sync import sync_job
from .utils import compute_content_hash


logger = logging.getLogger(__name__)


class SyncedModel(models.Model):
    first_synced_at = models.DateTimeField(null=True, editable=False)
    last_synced_at = models.DateTimeField(null=True, editable=False)

    class Meta:
        ordering = (models.F("first_synced_at").desc(nulls_first=True), "-pk")
        abstract = True


class Project(SyncedModel):
    """
    Represents a project in Smartling. There should normally only be one of
    these, it's synced from the Smartling API based on the PROJECT_ID setting.
    """

    environment = models.CharField(max_length=16)
    account_uid = models.CharField(max_length=32)
    archived = models.BooleanField()
    project_id = models.CharField(max_length=16)
    name = models.CharField(max_length=255)
    type_code = models.CharField(max_length=32)
    source_locale_description = models.CharField(max_length=255)
    source_locale_id = models.CharField(max_length=16)

    target_locales: Manager["ProjectTargetLocale"]

    class Meta(SyncedModel.Meta):
        unique_together = ["environment", "account_uid", "project_id"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(environment="production")
                | models.Q(environment="staging"),
                name="project_environment",
            )
        ]

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
                environment=smartling_settings.ENVIRONMENT,
                account_uid=project_details["accountUid"],
                project_id=project_details["projectId"],
            )
        except Project.DoesNotExist:
            project = cls(
                environment=smartling_settings.ENVIRONMENT,
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

        seen_target_locale_ids: set[str] = set()
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
    required=smartling_settings.REQUIRED,
    heading=_("Mark translation for Smartling processing"),
    enable_text=_("Click to mark for Smartling processing"),
    disable_text=_("Click to skip Smartling processing"),
)
class Job(SyncedModel):
    """
    Represents a job in Smartling and its links to TranslationSource and
    Translation objects in Wagtail.
    """

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
    # TODO record status of imported translations per `Translation`
    translations = models.ManyToManyField(
        Translation,
        related_name="smartling_jobs",
    )
    content_hash = models.CharField(max_length=64, blank=True)

    # Smartling job config fields

    name = models.CharField(max_length=170, editable=False)
    description = models.TextField(blank=True, editable=False)
    reference_number = models.CharField(max_length=170, editable=False)
    due_date = models.DateTimeField(blank=True, null=True)

    # Smartling API-derived fields

    translation_job_uid = models.CharField(max_length=64, editable=False)
    status = models.CharField(
        max_length=32,
        choices=JobStatus.choices,
        default=JobStatus.UNSYNCED,
        editable=False,
    )

    # Our fields

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="jobs")
    translations_imported_at = models.DateTimeField(null=True, editable=False)
    # NB - `file_uri`` isn't a field that the Smartling API returns. The
    # intended way to get this information is from the sourceFiles value in the
    # job details data or via the dedicated endpoint that lists file within a
    # job. However, we only ever have one file per job, so we store the URI of
    # that file here for convenience once it's been added.
    #
    # Refs:
    #   https://api-reference.smartling.com/#tag/Jobs/operation/getJobDetails
    #   https://api-reference.smartling.com/#tag/Jobs/operation/getJobFilesList
    #
    file_uri = models.CharField(max_length=255, blank=True, editable=False)

    base_form_class = JobForm
    panels = [FieldPanel("due_date")]

    class Meta(SyncedModel.Meta):
        default_permissions = ("view",)
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(
                        status=JobStatus.UNSYNCED,
                        first_synced_at__isnull=True,
                        last_synced_at__isnull=True,
                        translation_job_uid="",
                    )
                    | (
                        ~models.Q(status=JobStatus.UNSYNCED)
                        & models.Q(
                            first_synced_at__isnull=False,
                            last_synced_at__isnull=False,
                        )
                        & ~models.Q(translation_job_uid="")
                    )
                ),
                name="status_consistent_with_sync_dates",
            ),
        ]

    def __str__(self):
        return self.name

    @staticmethod
    def get_default_name(
        translation_source: TranslationSource,
        translations: Iterable[Translation],
    ) -> str:
        """
        Default name to use for the Job we want to create in Smartling. It needs
        to be unique else Smartling will reject the Job when we try to make it.

        Our template is`$OPTIONAL_PREFIX $UNIQUE_HASH $SOURCE_ID`

        * `$OPTIONAL_PREFIX` will be set via the `JOB_NAME_PREFIX` setting
        * `$UNIQUE_HASH` is an 8-character hash - not too long to be cumbersone,
           not too short to risk collisions. We hash the current timestamp plus
           relevant locale codes, then use the first 8 chars. (Collision risk
           is 1 in 4.2bn and we'll never generate that many jobs!)
        * `$SOURCE_ID` is the integer PK of the source object being translated.
           This is mainly because it gives us a simple, incrementing number that
           translators can use as a quick reference, but it can also be traced
           back to something in the CMS if we need to. Note that this ID alone
           will not be unique across all Jobs, because it will be re-used if a
           source object were to be amended and resubmitted for translation.
        """
        _timestamp = (
            timezone.now()
            .replace(tzinfo=None)  # remove +00:00 - we know it's UTC
            .isoformat(timespec="seconds")
        )
        _locales = ":".join(sorted(t.target_locale.language_code for t in translations))

        hash = hashlib.md5(
            _timestamp.encode("ascii") + _locales.encode("ascii"),
            usedforsecurity=False,
        ).hexdigest()[:8]

        name = f"{hash} #{translation_source.pk}"
        if smartling_settings.JOB_NAME_PREFIX:
            name = f"{smartling_settings.JOB_NAME_PREFIX} {name}"
        return name

    @staticmethod
    def get_default_reference_number(
        translation_source: TranslationSource,
        translations: Iterable[Translation],
    ) -> str:
        """
        Default reference number to use for the job in Smartling. This is just
        the translation_key for the source object. Setting this lets us easily
        look up all the Smartling jobs associated with a particular source
        object.
        """
        return f"{translation_source.object.translation_key}"

    @staticmethod
    def get_description(
        translation_source: TranslationSource,
        translations: Iterable[Translation],
    ) -> str:
        """
        Default description to use for the job in Smartling. This is
        human-readable and contains a link to the edit view for the translatable
        model.

        If the JOB_DESCRIPTION_CALLBACK setting is set to a function or importable
        string, it will be called with the default description, the translation source
        and the target translations. It is expected to return a string.
        """
        source_instance = translation_source.get_source_instance()
        ct_name = type(source_instance)._meta.verbose_name

        description = f"CMS translation job for {ct_name} '{source_instance}'."

        if callback_fn := smartling_settings.JOB_DESCRIPTION_CALLBACK:
            description = callback_fn(description, source_instance, translations)

        return description

    @classmethod
    def get_or_create_from_source_and_translation_data(
        cls,
        translation_source: TranslationSource,
        translations: Iterable[Translation],
        *,
        user: AbstractBaseUser,
        due_date: datetime | None,
    ) -> None:
        """
        This is the main entrypoint for creating Jobs. Jobs created here are in
        a pending state until the `sync_smartling` management command picks them
        up and creates a corresponding job in Smartling via the API.

        Jobs are only created if there's no pending or completed job for the provided
        TranslationSource.
        """
        # TODO only submit locales that match Smartling target locales
        # TODO make sure the source locale matches the Smartling project's language
        # TODO lookup existing jobs
        # TODO make sure existing job lookup only refers to current project

        project = Project.get_current()
        content_hash = compute_content_hash(translation_source.export_po())

        # Check whether we have any pending jobs for the same translation source content
        if Job.objects.filter(
            project=project,
            translation_source=translation_source,
            content_hash=content_hash,
            status__in=UNSYNCED_OR_PENDING_STATUSES,
        ).exists():
            return

        job = Job.objects.create(
            project=project,
            translation_source=translation_source,
            user=user,
            name=cls.get_default_name(translation_source, translations),
            description=cls.get_description(translation_source, translations),
            reference_number=cls.get_default_reference_number(
                translation_source, translations
            ),
            due_date=due_date,
            content_hash=content_hash,
        )
        job.translations.set(translations)

        if isinstance(background, ImmediateBackend):
            # Don't enqueue anything slow if we're using the dummy background
            # worker, let the `sync_smartling` management command pick things up
            # on a schedule instead.
            return

        # If we get here we've got a proper background worker, so we can safely
        # enqueue the syncing of the job.
        background.enqueue(sync_job, args=(job.pk,), kwargs={})


class LandedTranslationTaskManager(models.Manager):
    def incomplete(self):
        return self.filter(completed_on__isnull=True, cancelled_on__isnull=True)

    def create_from_source_and_translation(
        self,
        source_object: models.Model,
        translated_locale: Locale,
    ) -> "LandedTranslationTask":
        """
        Make a LandedTranslationTask for all users of the translation-approval
        group, for the target instance mentioned
        """

        c_type = ContentType.objects.get_for_model(source_object)

        task, created = LandedTranslationTask.objects.get_or_create(
            content_type=c_type,
            object_id=source_object.pk,
            relevant_locale=translated_locale,
        )
        action = "made" if created else "found"

        msg = (
            f"Translation-approval task {action} for {c_type.name}#{source_object.pk}"
            f" in {translated_locale.language_name}."
        )
        logger.info(msg)

        return task


class LandedTranslationTask(models.Model):
    """
    A custom task prompting members of a particular Group to review and
    publish a particular Page or Snippet, which has just had translations land.

    Note that this is _not_ a subclass of Task, and we don't it to sit
    within a workflow because Workflows a) don't support Snippets and b)
    are applied at a root or branching point, whereas we want these to be
    applied specifically for certain pages only, and not be auto-added
    to any potential child pages via a Workflow's cascade.
    """

    content_type = models.ForeignKey(
        ContentType,
        verbose_name=_("content type"),
        related_name="wagtail_localize_smartling_tasks",
        on_delete=models.CASCADE,
    )
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")

    relevant_locale = models.ForeignKey(
        Locale,
        null=False,
        on_delete=models.CASCADE,
    )

    created_on = models.DateTimeField(auto_now_add=True)
    completed_on = models.DateTimeField(null=True, blank=True)
    cancelled_on = models.DateTimeField(null=True, blank=True)

    objects = LandedTranslationTaskManager()

    def __str__(self):
        return f"LandedTranslationTask for {self.content_object} in {self.relevant_locale.language_name}"  # noqa: E501

    def __repr__(self):
        return f"<LandedTranslationTask: {self.content_type.name}#{self.object_id}>"

    def is_completed(self) -> bool:
        return bool(self.completed_on)

    def is_cancelled(self) -> bool:
        return bool(self.cancelled_on)
