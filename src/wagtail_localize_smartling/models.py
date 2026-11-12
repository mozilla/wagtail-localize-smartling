import hashlib
import logging
import uuid

from collections.abc import Iterable
from datetime import datetime
from functools import lru_cache

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Case, Count, ExpressionWrapper, F, OuterRef, Q, QuerySet, Subquery, Value, When
from django.db.models.functions import Coalesce
from django.db.models.manager import Manager
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from wagtail.admin.panels import FieldPanel
from wagtail.models import Locale, Page
from wagtail_localize.components import register_translation_component
from wagtail_localize.models import Translation, TranslationSource
from wagtail_localize.tasks import ImmediateBackend, background

from . import utils as smartling_utils
from .api.client import client
from .api.types import JobStatus
from .constants import (
    AWAITING_AUTHORIZATION_STATUSES,
    EXPANDABLE_JOB_STATUSES,
    UNSYNCED_OR_PENDING_STATUSES,
    UNTRANSLATED_STATUSES,
)
from .forms import JobForm
from .settings import settings as smartling_settings
from .sync import sync_job
from .utils import compute_content_hash, get_snippet_admin_url


logger = logging.getLogger(__name__)

# A failing Smartling call can answer with a whole HTML error page, and the
# message is written to the job on every sweep and shown in the admin.
SYNC_ERROR_MAX_LENGTH = 2000


class SyncedModel(models.Model):
    first_synced_at = models.DateTimeField(null=True, editable=False)
    last_synced_at = models.DateTimeField(null=True, editable=False)

    class Meta:
        ordering = (models.F("first_synced_at").desc(nulls_first=True), "-pk")
        abstract = True


class JobTranslation(models.Model):
    """
    Through model for Job.translations M2M that tracks per-locale import status.

    This enables tracking which individual locale translations have been imported,
    allowing us to import completed locales before the entire job is finished.
    """

    job = models.ForeignKey(
        "Job",
        on_delete=models.CASCADE,
        related_name="job_translations",
    )
    translation = models.ForeignKey(
        Translation,
        on_delete=models.CASCADE,
        related_name="smartling_job_translations",
    )
    imported_at = models.DateTimeField(null=True, editable=False)
    content_hash = models.CharField(max_length=64, blank=True, editable=False)

    class Meta:
        unique_together = ["job", "translation"]

    def __str__(self):
        return f"JobTranslation({self.job.pk}, {self.translation.pk})"


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
                condition=models.Q(environment="production") | models.Q(environment="staging"),
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


class OrderState(models.TextChoices):
    NOT_SENT = "not_sent", _("Not sent to Smartling")
    FAILED = "failed", _("Submission failed")
    AWAITING_AUTHORIZATION = "awaiting", _("Awaiting authorization")
    IN_TRANSLATION = "in_translation", _("In translation")
    PARTIALLY_LANDED = "partially_landed", _("Partially landed")
    COMPLETE = "complete", _("Complete")
    CANCELLED = "cancelled", _("Cancelled")


# The states that say an order's last send produced nothing: the
# job was cancelled or deleted at Smartling, or gave up before it got there.
ORDER_STATES_NEEDING_ATTENTION = (OrderState.FAILED, OrderState.CANCELLED)


def get_order_state_case() -> Case:
    """Return the Case() object to determine the Order's state."""
    return Case(
        When(submitted_at__isnull=True, then=Value(OrderState.NOT_SENT)),
        When(latest_job_status__isnull=True, then=Value(OrderState.NOT_SENT)),
        When(
            Q(total_page_locale_pairs__gt=0) & Q(landed_page_locale_pairs=F("total_page_locale_pairs")),
            then=Value(OrderState.COMPLETE),
        ),
        When(latest_job_blocked_before_sending=True, then=Value(OrderState.FAILED)),
        When(latest_job_status__in=UNTRANSLATED_STATUSES, then=Value(OrderState.CANCELLED)),
        When(landed_page_locale_pairs__gt=0, then=Value(OrderState.PARTIALLY_LANDED)),
        When(
            latest_job_status__in=AWAITING_AUTHORIZATION_STATUSES,
            then=Value(OrderState.AWAITING_AUTHORIZATION),
        ),
        default=Value(OrderState.IN_TRANSLATION),
        output_field=models.CharField(),
    )


class Order(models.Model):
    """
    A batch of pages submitted to Smartling together, with the metadata that
    tells translators what they are looking at.

    An Order holds the data for Smartling's concept of a "job", but it is
    specific to this library:
      - an Order is created here, and can be edited before sending to Smartling
        (before Smartling creates its job for it)
      - an Order can actually have multiple Smartling jobs, because it allows
        resubmitting. So, a user may submit an Order, get an error from
        Smartling, then resubmit the Order. In this library, that is considered
        the same Order. In Smartling, it is considered different jobs.
    """

    # Shorter than Job.name because the job's name is built from this one plus
    # the configured prefix and a resend suffix, and Smartling rejects a name
    # over its own limit.
    name = models.CharField(
        max_length=100,
        help_text=_("Becomes the Smartling job name when the order is submitted."),
    )
    notes = models.TextField(
        blank=True,
        help_text=_("Sent to Smartling as the job description."),
    )
    due_date = models.DateTimeField(
        blank=True,
        null=True,
        help_text=_("Sent to Smartling as the job deadline, unless it has already passed."),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    # Everyone who added a page, which is what reaches translators in the job
    # description. Distinct from created_by, who only opened the order.
    contributors = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="+", blank=True)
    submitted_at = models.DateTimeField(null=True, editable=False)
    reference_number = models.CharField(
        max_length=170,
        editable=False,
        help_text=(
            "Sent to Smartling as the reference number of every job this order creates. "
            "Smartling shows it for each job, which connects it to the Order it came from."
        ),
    )

    # The fields whose values reach translators: the job's name, description
    # and due date are built from these. Not contributors, which also reaches
    # the description but, as an M2M, changes after the row is saved.
    FIELDS_SENT_TO_SMARTLING = frozenset({"name", "notes", "due_date"})

    jobs: Manager["Job"]
    order_items: Manager["OrderItem"]
    # The items the last submit() left out because nothing in them remained to
    # translate. Not a field: it describes one send, not the order.
    dropped_order_items: "list[OrderItem]"
    # The due date the last submit() left out because it had already passed,
    # so that the caller can say which date the pages went without.
    dropped_due_date: "datetime | None"

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.reference_number:
            # Stable for the life of the order so that every send of it can
            # carry one number to Smartling, whose UI has no view of the FK.
            self.reference_number = uuid.uuid4().hex

        # Clean the data being saved before saving.
        self.clean()
        super().save(*args, **kwargs)

    def clean(self):
        """Refuse to rewrite what a sent order told translators."""
        super().clean()
        # No further cleaning for Orders that are unsaved or draft.
        if self.pk is None or self.is_draft:
            return

        # If the Order has been deleted, no further cleaning is done.
        stored = Order.objects.filter(pk=self.pk).values(*self.FIELDS_SENT_TO_SMARTLING).first()
        if stored is None:
            return

        changed = sorted(field for field, value in stored.items() if getattr(self, field) != value)
        if changed:
            raise ValidationError(
                # A sent order is the record of what went to Smartling, so the
                # fields translators were given have to keep saying what they said.
                f"This order was sent to Smartling and can no longer be edited ({', '.join(changed)})."
            )

    @property
    def is_draft(self) -> bool:
        return self.submitted_at is None

    @property
    def dropped_page_names(self) -> str:
        """The pages the last submit() left out."""
        return ", ".join(item.translation_source.object_repr for item in self.dropped_order_items)

    def get_latest_job(self) -> "Job | None":
        """Return the most recent Job created for this order."""
        # Note: this is not self.jobs.first() because Job orders by
        # first_synced_at with nulls first.
        return self.jobs.order_by("-pk").first()

    @classmethod
    def annotate_state(cls, queryset: "QuerySet[Order]") -> "QuerySet[Order]":
        """Annotate an "annotated_state" field for the Orders in the queryset."""
        # Ordered by pk, not by Job.Meta.ordering, which puts a job that never
        # reached Smartling ahead of the one that replaced it.
        latest_job = Job.objects.filter(order=OuterRef("pk")).order_by("-pk")
        # A pair is one page in one target locale
        page_locale_pairs = JobTranslation.objects.filter(job__order=OuterRef("pk"))
        landed_page_locale_pairs = page_locale_pairs.filter(imported_at__isnull=False)
        return queryset.annotate(
            latest_job_status=Subquery(latest_job.values("status")[:1]),
            latest_job_blocked_before_sending=Subquery(
                latest_job.annotate(
                    blocked_before_sending=ExpressionWrapper(
                        Q(sync_blocked=True, translation_job_uid=""),
                        output_field=models.BooleanField(),
                    )
                ).values("blocked_before_sending")[:1]
            ),
            total_page_locale_pairs=Coalesce(Subquery(cls._get_distinct_page_locale_pair_count(page_locale_pairs)), 0),
            landed_page_locale_pairs=Coalesce(
                Subquery(cls._get_distinct_page_locale_pair_count(landed_page_locale_pairs)), 0
            ),
        ).annotate(annotated_state=get_order_state_case())

    @staticmethod
    def _get_distinct_page_locale_pair_count(job_translations: "QuerySet[JobTranslation]") -> "QuerySet":
        return (
            job_translations.order_by()
            .values("job__order")
            .annotate(page_locale_pair_count=Count("translation", distinct=True))
            .values("page_locale_pair_count")[:1]
        )

    @property
    def state(self) -> OrderState:
        """The state that this Order is in."""
        if self.pk is None:
            # The Order has not been saved, which means that it has not been
            # sent. Having this check here avoids an error in the
            # self.annotate_state() line below.
            return OrderState.NOT_SENT

        # If the Order already has an "annotated_state", then use it; otherwise,
        # determine the annotated_state.
        if (annotated_state := getattr(self, "annotated_state", None)) is None:
            annotated_state = (
                self.annotate_state(Order.objects.filter(pk=self.pk)).values_list("annotated_state", flat=True).get()
            )

        return OrderState(annotated_state)

    @property
    def state_label(self) -> str:
        return self.state.label

    def suggested_job_name(self) -> str:
        """
        A Smartling job name for the next send of this order.

        Smartling rejects a duplicate job name, so a resend cannot reuse the
        first one.
        """
        send_number = self.jobs.count() + 1
        name = self.name if send_number == 1 else f"{self.name}-{send_number}"
        if smartling_settings.JOB_NAME_PREFIX:
            name = f"{smartling_settings.JOB_NAME_PREFIX} {name}"
        return name

    def submit(
        self,
        *,
        user: AbstractBaseUser,
        job_name: str | None = None,
        items: "Iterable[OrderItem] | None" = None,
        translations: Iterable[Translation] | None = None,
    ) -> "Job":
        """
        Create the one Smartling job that carries every page of this order.

        Returns the new Job. A resend passes the items and translations that
        still need sending, and a name of its own, because Smartling rejects a
        duplicate job name. Whatever this send leaves behind is put on
        `dropped_order_items` and `dropped_due_date` for the caller to report.
        """
        order_items = list(items if items is not None else self.order_items.select_related("translation_source"))
        wanted_translations = set(
            translations
            if translations is not None
            else Translation.objects.filter(smartling_order_items__order=self, enabled=True)
        )

        # A due date that has already passed reaches translators as overdue on
        # arrival, which is worse than none.
        due_date = self.due_date if self.due_date and self.due_date >= timezone.now() else None
        self.dropped_due_date = self.due_date if due_date is None else None

        order_items_to_send = []
        self.dropped_order_items = []
        for order_item in order_items:
            wanted_for_order_item = [
                translation
                for translation in wanted_translations
                if translation.source_id == order_item.translation_source_id  # pyright: ignore[reportAttributeAccessIssue]
            ]
            if wanted_for_order_item:
                order_items_to_send.append((order_item, wanted_for_order_item))
            else:
                # Smartling cannot authorize a file with no strings in it, and
                # one such file leaves the whole job stuck.
                self.dropped_order_items.append(order_item)

        if not order_items_to_send:
            raise ValueError(f"Order {self.pk} has no pages to translate")

        with transaction.atomic():
            job = Job.objects.create(
                project=Project.get_current(),
                order=self,
                user=user,
                name=job_name or self.suggested_job_name(),
                reference_number=self.reference_number,
                due_date=due_date,
            )
            for order_item, _ in order_items_to_send:
                JobFile.objects.create(
                    job=job,
                    translation_source=order_item.translation_source,
                    # Hashed now rather than when the page was added, which may
                    # be days earlier: Smartling is sent what the page says
                    # today, and the hash has to describe what was sent.
                    content_hash=compute_content_hash(order_item.translation_source.export_po()),
                )
            job.translations.set(
                [
                    translation
                    for _, wanted_for_order_item in order_items_to_send
                    for translation in wanted_for_order_item
                ]
            )
            # Described last: the description names the files, so it cannot be
            # written until they exist.
            job.description = Job.get_description_for_order(self, job=job)
            job.save(update_fields=["description"])

            if self.is_draft:
                self.submitted_at = timezone.now()
                self.save(update_fields=["submitted_at"])

            if not isinstance(background, ImmediateBackend):
                # on_commit, not a bare enqueue: this runs inside the submitting
                # view's own transaction, and a worker that picked the job up
                # before that committed would find no job at all.
                transaction.on_commit(lambda: background.enqueue(sync_job, args=(job.pk,), kwargs={}))

        return job


class OrderItem(models.Model):
    """A page in an order, recorded while the order is still being assembled."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="order_items")
    translation_source = models.ForeignKey(TranslationSource, on_delete=models.CASCADE, related_name="+")
    # What the editor actually ticked, after the excluded locales and the
    # already-translated ones have been taken out. A draft order has no Job and
    # so no JobTranslation, and re-deriving this from the source would put both
    # of those back.
    translations = models.ManyToManyField(Translation, related_name="smartling_order_items")
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="+",
    )
    added_at = models.DateTimeField(auto_now_add=True)

    translation_source_id: int

    class Meta:
        unique_together = [("order", "translation_source")]
        ordering = ["added_at"]

    def __str__(self):
        return f"{self.translation_source} in {self.order}"


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
    order = models.ForeignKey(
        "Order",
        on_delete=models.PROTECT,
        related_name="jobs",
        null=True,
        blank=True,
    )
    # Set while a worker is pushing this job to Smartling, so a second worker
    # can tell a job waiting to be pushed from one already in flight.
    sync_started_at = models.DateTimeField(null=True, editable=False)
    translation_source = models.ForeignKey(
        TranslationSource,
        on_delete=models.CASCADE,
        related_name="smartling_jobs",
        null=True,
        blank=True,
    )
    translations = models.ManyToManyField(
        Translation,
        through="JobTranslation",
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
    # Sometimes, we need to resume a (Smartling) batch, so we record the batch uid here.
    batch_uid = models.CharField(max_length=64, blank=True, editable=False)
    status = models.CharField(
        max_length=32,
        choices=JobStatus.choices,
        default=JobStatus.UNSYNCED,
        editable=False,
    )

    # Our fields

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="jobs")
    translations_imported_at = models.DateTimeField(null=True, editable=False)
    # NB - `file_uri` isn't a field that the Smartling API returns. The
    # intended way to get this information is from the sourceFiles value in the
    # job details data or via the dedicated endpoint that lists files within a
    # job, so the URI is stored here for convenience once the upload returns it.
    #
    # Refs:
    #   https://api-reference.smartling.com/#tag/Jobs/operation/getJobDetails
    #   https://api-reference.smartling.com/#tag/Jobs/operation/getJobFilesList
    #
    file_uri = models.CharField(max_length=255, blank=True, editable=False)
    # Error-related fields
    last_sync_error = models.TextField(blank=True)
    last_sync_error_at = models.DateTimeField(null=True, editable=False)
    consecutive_sync_failures = models.PositiveIntegerField(default=0)
    # Set when a job cannot succeed by being retried, so we can avoid retrying
    # it.
    sync_blocked = models.BooleanField(default=False)

    files: Manager["JobFile"]
    job_translations: Manager["JobTranslation"]

    base_form_class = JobForm
    panels = [FieldPanel("due_date")]

    class Meta(SyncedModel.Meta):
        default_permissions = ("view",)
        constraints = [
            models.CheckConstraint(
                condition=(
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

    def record_sync_failure(self, exc: Exception, *, permanent: bool, pushing: bool) -> None:
        """
        Persist a failed sync attempt and stop retrying if it cannot succeed.

        Only a failed push blocks the job. Once its files are at Smartling the
        job is polled on every sweep whatever happens, so blocking it would
        stop nothing while hiding a translation that can still land.

        A transient failure keeps retrying until SYNC_FAILURE_LIMIT, so a job
        whose Smartling project is unreachable does not retry forever either.
        """
        message = str(exc)
        if len(message) > SYNC_ERROR_MAX_LENGTH:
            message = message[: SYNC_ERROR_MAX_LENGTH - 1] + "\u2026"
        self.last_sync_error = message
        self.last_sync_error_at = timezone.now()
        self.consecutive_sync_failures += 1
        if pushing and (permanent or self.consecutive_sync_failures >= smartling_settings.SYNC_FAILURE_LIMIT):
            self.sync_blocked = True
        self.save(
            update_fields=[
                "last_sync_error",
                "last_sync_error_at",
                "consecutive_sync_failures",
                "sync_blocked",
            ]
        )

    def clear_sync_failure(self) -> None:
        """Forget a past failure, so a healthy job does not read as a failing one."""
        if not (self.last_sync_error or self.last_sync_error_at or self.consecutive_sync_failures or self.sync_blocked):
            return

        self.last_sync_error = ""
        self.last_sync_error_at = None
        self.consecutive_sync_failures = 0
        self.sync_blocked = False
        self.save(
            update_fields=[
                "last_sync_error",
                "last_sync_error_at",
                "consecutive_sync_failures",
                "sync_blocked",
            ]
        )

    @property
    def push_is_incomplete(self) -> bool:
        """
        Whether this job still needs to send something to Smartling.

        A push is incomplete until the job exists in Smartling and every one of
        its files has been uploaded.
        """
        return self.translation_job_uid == "" or self.files.filter(file_uri="").exists()

    def pending_translations(self) -> "QuerySet[Translation]":
        """The translations this job was given that are still wanted and still missing."""
        return Translation.objects.filter(
            pk__in=self.job_translations.filter(imported_at__isnull=True).values("translation"),
            enabled=True,
        )

    @property
    def needs_a_new_job(self) -> bool:
        """
        Whether these translations can now only arrive via a new job.

        A job blocked after its files reached Smartling is not one of those: its
        locales can still land, and a second job would duplicate them.
        """
        return self.status in UNTRANSLATED_STATUSES or (self.sync_blocked and not self.translation_job_uid)

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
    def get_description_for_order(order: "Order", *, job: "Job") -> str:
        """
        Default description to use for the job in Smartling. This is
        human-readable and names the order, the pages this job carries, whoever
        assembled it, and whatever the editor wrote.
        """
        lines = [f"Order: {order.name}", ""]

        for job_file in job.files.select_related("translation_source"):
            source_instance = job_file.translation_source.get_source_instance()
            lines.append(f"- {type(source_instance)._meta.verbose_name} '{source_instance}'")

        contributors = [contributor.get_username() for contributor in order.contributors.all()]
        if contributors:
            lines += ["", f"Assembled by: {', '.join(contributors)}"]

        if order.notes:
            lines += ["", order.notes]

        description = "\n".join(lines)

        if callback_fn := smartling_settings.JOB_DESCRIPTION_CALLBACK:
            description = callback_fn(description, job)

        return description

    @classmethod
    def get_or_create_from_source_and_translation_data(
        cls,
        translation_source: TranslationSource,
        translations: Iterable[Translation],
        *,
        user: AbstractBaseUser,
        due_date: datetime | None,
        order: "Order | None" = None,
    ) -> None:
        """
        This is the main entrypoint for creating Jobs. Jobs created here are in
        a pending state until the `sync_smartling` management command picks them
        up and creates a corresponding job in Smartling via the API.

        If there's already a pending job for the same content:
        - If the job is in an expandable state (UNSYNCED, DRAFT, AWAITING_AUTHORIZATION),
          new locales will be added to that job.
        - If the job is IN_PROGRESS, a new parallel job will be created for the
          new locales.

        If all requested locales are already covered by existing jobs, no action is taken.

        When an order is given, the page joins that order instead and no job is
        created here: the order's own job is created when it is submitted.
        """
        # TODO only submit locales that match Smartling target locales
        # TODO make sure the source locale matches the Smartling project's language

        translations_list = list(translations)

        # Filter out translations for excluded locales
        excluded = smartling_settings.EXCLUDE_LOCALES
        if excluded:
            excluded_translations = [t for t in translations_list if t.target_locale.language_code in excluded]
            if excluded_translations:
                logger.info(
                    "Excluding %d translation(s) for locales: %s",
                    len(excluded_translations),
                    ", ".join(t.target_locale.language_code for t in excluded_translations),
                )
            translations_list = [t for t in translations_list if t.target_locale.language_code not in excluded]
            if not translations_list:
                return

        if order is not None and not order.is_draft:
            logger.info(
                "Order %s has already been submitted, sending %s as its own job",
                order.pk,
                translation_source,
            )
            order = None

        if order is not None:
            # If order is passed, then the page joins the order, and
            # nothing is sent to Smartling until the order itself is.
            order_item, _ = OrderItem.objects.get_or_create(
                order=order,
                translation_source=translation_source,
                defaults={"added_by": user},
            )
            # Added to rather than replaced: the same page can be put in an
            # order twice, for different locales each time.
            order_item.translations.add(*translations_list)
            order.contributors.add(user)
            return

        project = Project.get_current()
        content_hash = compute_content_hash(translation_source.export_po())
        remaining_translations = {t.target_locale.pk: t for t in translations_list}

        # Find existing pending jobs for the same source content
        existing_jobs = [
            existing_job
            for existing_job in cls.objects.filter(
                project=project,
                files__translation_source=translation_source,
                files__content_hash=content_hash,
                status__in=UNSYNCED_OR_PENDING_STATUSES,
            )
            # Exclude Jobs that are blocked and never reached Smartling.
            .exclude(sync_blocked=True, translation_job_uid="")
            .prefetch_related("translations", "files")
            .distinct()
            # A job that carries other pages belongs to a different order.
            if {job_file.translation_source_id for job_file in existing_job.files.all()} == {translation_source.pk}
        ]

        for existing_job in existing_jobs:
            # Get locale IDs already covered by this job
            existing_locale_ids = set(existing_job.translations.values_list("target_locale", flat=True))

            # Find translations that aren't already in this job
            new_translations_for_job = [
                t for locale_id, t in remaining_translations.items() if locale_id not in existing_locale_ids
            ]

            if not new_translations_for_job:
                # All remaining translations are covered by this job
                remaining_translations.clear()
                break

            # Try to add new locales if the job is in an expandable state
            if existing_job.status in EXPANDABLE_JOB_STATUSES:
                _add_locales_to_existing_job(
                    existing_job,
                    translation_source,
                    new_translations_for_job,
                    user=user,
                )
                for t in new_translations_for_job:
                    remaining_translations.pop(t.target_locale.pk, None)

                if not remaining_translations:
                    break

        # Create a new job for any remaining locales not covered by existing jobs
        if remaining_translations:
            new_translations = list(remaining_translations.values())
            # Create Job and JobFile in 1 transaction to avoid having an invalid
            # Job (that later gets sent to Smartling).
            with transaction.atomic():
                # Sending one page now is an order of one, submitted the moment
                # it is made, so that every job has an order behind it.
                job_order = Order.objects.create(
                    # The prefix belongs to the Smartling job name built from
                    # this one, not to the order an editor sees listed.
                    name=str(translation_source.get_source_instance())[:100],
                    created_by=user,
                    due_date=due_date,
                    submitted_at=timezone.now(),
                )
                job_order.contributors.add(user)
                # Create the OrderItem, because without the OrderItem the order
                # holds no pages, which is what the order screens count and list.
                order_item = OrderItem.objects.create(
                    order=job_order,
                    translation_source=translation_source,
                    added_by=user,
                )
                order_item.translations.set(new_translations)
                job = cls.objects.create(
                    project=project,
                    order=job_order,
                    translation_source=translation_source,
                    user=user,
                    name=cls.get_default_name(translation_source, new_translations),
                    reference_number=cls.get_default_reference_number(translation_source, new_translations),
                    due_date=due_date,
                    content_hash=content_hash,
                )
                JobFile.objects.create(
                    job=job,
                    translation_source=translation_source,
                    content_hash=content_hash,
                )
                job.translations.set(new_translations)
                job.description = cls.get_description_for_order(job_order, job=job)
                job.save(update_fields=["description"])

            if isinstance(background, ImmediateBackend):
                # Don't enqueue anything slow if we're using the dummy background
                # worker, let the `sync_smartling` management command pick things up
                # on a schedule instead.
                return

            # If we get here we've got a proper background worker, so we can safely
            # enqueue the syncing of the job.
            background.enqueue(sync_job, args=(job.pk,), kwargs={})


class JobFile(models.Model):
    """
    One translatable object within a Job  (one .po file uploaded to Smartling).

    A Job can reference several TranslationSource, because an order is a batch
    of pages submitted together.
    """

    job = models.ForeignKey("Job", on_delete=models.CASCADE, related_name="files")
    translation_source = models.ForeignKey(
        TranslationSource,
        on_delete=models.CASCADE,
        related_name="smartling_job_files",
    )
    file_uri = models.CharField(max_length=255, blank=True, editable=False)
    content_hash = models.CharField(max_length=64, blank=True)

    job_id: int
    translation_source_id: int

    class Meta:
        unique_together = [("job", "translation_source")]
        # Make sure the order is always the same, since several pieces of logic
        # walk a job's files and pair them up positionally.
        ordering = ["pk"]

    def __str__(self):
        return self.file_uri or f"file for source {self.translation_source_id}"


def _add_locales_to_existing_job(
    job: "Job",
    translation_source: TranslationSource,
    translations: list[Translation],
    *,
    user: AbstractBaseUser,
) -> None:
    """
    Add new locales to an existing job and to the order behind it.

    For UNSYNCED jobs, the translations are simply added to the M2M relationship.
    For synced jobs (DRAFT, AWAITING_AUTHORIZATION), the Smartling API is called
    to add the new locales to the job.

    Uses transactions and savepoints to ensure consistency between the database
    and Smartling API state. The order is recorded inside them, so a locale the
    job keeps is a locale the order knows about.
    """
    if job.status == JobStatus.UNSYNCED:
        # Job hasn't been synced yet - just add to M2M in a single transaction
        with transaction.atomic():
            for translation in translations:
                JobTranslation.objects.create(job=job, translation=translation)
            _record_locales_on_order(job, translation_source, translations, user=user)
        logger.info(
            "Added %d locale(s) to unsynced job %s",
            len(translations),
            job,
        )
    else:
        # Job has been synced - need to call Smartling API to add locales
        # Use savepoints to ensure DB and API stay consistent:
        # - Create DB record first (inside savepoint)
        # - Then call API - if this fails, savepoint rolls back the DB create
        with transaction.atomic():
            for translation in translations:
                locale_id = smartling_utils.format_smartling_locale_id(translation.target_locale.language_code)
                sid = transaction.savepoint()
                try:
                    # Create DB records first (inside savepoint)
                    JobTranslation.objects.create(job=job, translation=translation)
                    _record_locales_on_order(job, translation_source, [translation], user=user)

                    # Then call API - if this fails, savepoint rolls back the DB creates
                    client.add_locale_to_job(job=job, locale_id=locale_id)

                    transaction.savepoint_commit(sid)
                    logger.info(
                        "Added locale %s to job %s via Smartling API",
                        locale_id,
                        job,
                    )
                except Exception:
                    transaction.savepoint_rollback(sid)
                    logger.exception(
                        "Failed to add locale %s to job %s",
                        locale_id,
                        job,
                    )
                    raise


def _record_locales_on_order(
    job: "Job",
    translation_source: TranslationSource,
    translations: list[Translation],
    *,
    user: AbstractBaseUser,
) -> None:
    """
    Record the locales the job has on the job's order.

    An order's items are what a resend reads to decide what to send, so an order
    that previously only knew about some of the job's locales needs to add the
    rest of the job's locales now.
    """
    if job.order_id is None:
        return

    order_item, created = OrderItem.objects.get_or_create(
        order_id=job.order_id,
        translation_source=translation_source,
        defaults={"added_by": user},
    )
    if created:
        # A page the order has no item for is owed every locale its job
        # carries, not only the ones arriving now.
        order_item.translations.set(job.translations.all())
    else:
        order_item.translations.add(*translations)


class LandedTranslationTaskManager(models.Manager):
    def incomplete(self):
        return self.filter(
            completed_on__isnull=True,
            cancelled_on__isnull=True,
        )

    def create_from_source_and_translation(
        self,
        source_object: models.Model,
        translated_locale: Locale,
    ) -> "LandedTranslationTask":
        """
        Make a LandedTranslationTask for all users of the translation-approval
        group, for the relevant translation of the source object.

        Note that the source object is the instance that was translated (from
        the Job), not the resulting translation. This is why we need to look the
        latter up via the relevant locale.

        We do store the resulting translated object (e.g. Page or Snippet) as
        the target of the generic FK.
        """

        translated_object = source_object.get_translations().get(  # pyright: ignore[reportAttributeAccessIssue]
            locale=translated_locale
        )

        c_type = ContentType.objects.get_for_model(translated_object)

        task, created = LandedTranslationTask.objects.get_or_create(
            content_type=c_type,
            object_id=translated_object.pk,
            relevant_locale=translated_locale,
            completed_on__isnull=True,
            cancelled_on__isnull=True,
        )
        action = "made" if created else "found"

        msg = (
            f"Translation-approval task {action} for {c_type.name}#{translated_object.pk}"
            f" in {translated_locale.language_name}."
        )
        logger.info(msg)

        return task


class LandedTranslationTask(models.Model):
    """
    A custom task prompting members of a particular Group to review and
    publish a particular Page or Snippet, which has just had translations land.

    Note that this is _not_ a subclass of Task, and we don't want it to sit
    within a workflow because Workflows are applied at a root or branching point,
    whereas we want these to be applied specifically for certain pages only, and
    not be auto-added to any potential child pages via a Workflow's cascade.
    """

    content_type = models.ForeignKey(
        ContentType,
        verbose_name=_("content type"),
        related_name="wagtail_localize_smartling_tasks",
        on_delete=models.CASCADE,
    )
    object_id = models.PositiveIntegerField()

    # content_object points to the translated item of content that this
    # task is for:
    content_object = GenericForeignKey("content_type", "object_id")

    relevant_locale = models.ForeignKey(
        # Denormed locale field to make ORM lookups simpler
        Locale,
        null=False,
        on_delete=models.CASCADE,
    )

    created_on = models.DateTimeField(auto_now_add=True)
    completed_on = models.DateTimeField(null=True, blank=True)
    cancelled_on = models.DateTimeField(null=True, blank=True)

    objects = LandedTranslationTaskManager()

    def __str__(self):
        return (
            "LandedTranslationTask for "
            f"{self.content_object} (#{self.object_id}) in {self.relevant_locale.language_name}"
        )

    def __repr__(self):
        return f"<LandedTranslationTask: {self.content_type.name}#{self.object_id}>"

    def edit_url_for_translated_item(self):
        if isinstance(self.content_object, Page):
            edit_url = reverse("wagtailadmin_pages:edit", args=[self.object_id])
        else:
            edit_url = get_snippet_admin_url(self.content_object)

        return edit_url

    def complete(self):
        self.completed_on = timezone.now()
        self.cancelled_on = None
        self.save(update_fields=["completed_on", "cancelled_on"])
        logger.info(f"LandedTranslationTask{self.pk} completed")  # TODO: add Wagtail log so we know who did this

    def cancel(self):
        self.completed_on = None
        self.cancelled_on = timezone.now()
        self.save(update_fields=["completed_on", "cancelled_on"])
        logger.info(f"LandedTranslationTask{self.pk} cancelled")
        # TODO: add Wagtail log so we know who did this

    def is_completed(self) -> bool:
        return bool(self.completed_on)

    def is_cancelled(self) -> bool:
        return bool(self.cancelled_on)
