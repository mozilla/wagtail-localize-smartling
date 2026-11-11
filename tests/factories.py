from typing import Any

import factory
import factory.django

from django.utils import timezone
from wagtail.models import Locale
from wagtail_localize.models import Translation, TranslationSource

from wagtail_localize_smartling import models as wls_models
from wagtail_localize_smartling.api.types import JobStatus
from wagtail_localize_smartling.settings import settings as smartling_settings

from testapp.factories import InfoPageFactory, TranslationSourceFactory, UserFactory


class ProjectFactory(factory.django.DjangoModelFactory):
    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        model = wls_models.Project
        django_get_or_create = ("environment", "account_uid", "project_id")

    environment = factory.LazyFunction(lambda: smartling_settings.ENVIRONMENT)
    account_uid = "dummy_account_uid"
    archived = False
    project_id = factory.LazyFunction(lambda: smartling_settings.PROJECT_ID)
    name = "Dummy project"
    type_code = "APPLICATION_RESOURCES"
    source_locale_description = "English (United States) [en-US]"
    source_locale_id = "en-US"
    first_synced_at = factory.LazyFunction(timezone.now)
    last_synced_at = factory.LazyFunction(timezone.now)


class OrderFactory(factory.django.DjangoModelFactory):
    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        model = wls_models.Order

    name = factory.Sequence(lambda n: f"Order {n}")
    created_by = factory.SubFactory(UserFactory)
    submitted_at = factory.LazyFunction(timezone.now)


class OrderItemFactory(factory.django.DjangoModelFactory):
    """
    When calling this factory, you must pass in a translation_source kwarg:
    OrderItem requires one and this factory builds no default.
    """

    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        model = wls_models.OrderItem

    order = factory.SubFactory(OrderFactory)
    added_by = factory.SubFactory(UserFactory)

    @factory.post_generation
    def translations(obj: wls_models.OrderItem, create: bool, extracted: Any, **kwargs):  # pyright: ignore[reportGeneralTypeIssues]
        if create and extracted is not None:
            obj.translations.set(extracted)


class JobFactory(factory.django.DjangoModelFactory):
    """
    When calling this factory, you must pass in a source_instance kwarg that is
    an instance of TranslatableMixin. This will be passed on to
    TranslationSourceFactory.
    """

    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        model = wls_models.Job
        exclude = ("source_instance",)

    class Params:
        unsynced = factory.Trait(translation_job_uid="")
        # A status alone violates status_consistent_with_sync_dates; a uid and
        # dates alone violate it the other way. The trait has to set all four,
        # and callers override only the status.
        synced = factory.Trait(
            status=JobStatus.AWAITING_AUTHORIZATION,
            translation_job_uid=factory.Sequence(lambda n: f"smartling-job-{n}"),
            first_synced_at=factory.LazyFunction(timezone.now),
            last_synced_at=factory.LazyFunction(timezone.now),
        )

    project = factory.SubFactory(ProjectFactory)

    user = factory.SubFactory(UserFactory)
    order = factory.SubFactory(OrderFactory, created_by=factory.SelfAttribute("..user"))
    translation_source = factory.SubFactory(
        TranslationSourceFactory,
        source_instance=factory.SelfAttribute("..source_instance"),
    )

    name = factory.Sequence(lambda n: f"Job {n}")
    description = factory.SelfAttribute("name")
    due_date = None

    # Declared before `translations`, which reads obj.files: factory_boy runs
    # post-generation hooks in declaration order, and the wrong order creates
    # no Translation rows at all, silently.
    @factory.post_generation
    def files(obj: wls_models.Job, create: bool, extracted: Any, **kwargs):  # pyright: ignore[reportGeneralTypeIssues]
        if not create:
            return

        # `extracted is not None` for the same reason as the hook below:
        # files=[] means "no files", not "the default one".
        sources = extracted if extracted is not None else [obj.translation_source]
        for translation_source in sources:
            wls_models.JobFile.objects.create(job=obj, translation_source=translation_source)

    @factory.post_generation
    def translations(obj: wls_models.Job, create: bool, extracted: Any, **kwargs):  # pyright: ignore[reportGeneralTypeIssues]
        if not create:
            return

        # `extracted is not None`, not `if extracted`: translations=[] means
        # "none", and a truthiness test silently creates the full default set
        # instead, which then collides with whatever the test creates by hand.
        if extracted is not None:
            obj.translations.set(extracted)
            return

        for job_file in obj.files.all():
            source = job_file.translation_source
            for target_locale in Locale.objects.exclude(pk=source.locale.pk):
                # Translation is unique on (source, target_locale), and the
                # get_or_create on the source factories means two jobs built
                # for the same page share one source. A plain create raises on
                # the second job.
                translation, _ = Translation.objects.get_or_create(source=source, target_locale=target_locale)
                obj.translations.add(translation)


class TranslationApproverGroupFactory(factory.django.DjangoModelFactory):
    class Meta:  # type: ignore
        model = "auth.Group"
        django_get_or_create = ("name",)

    name = smartling_settings.TRANSLATION_APPROVER_GROUP_NAME


class WagtailUserFactory(factory.django.DjangoModelFactory):
    class Meta:  # type: ignore
        model = "auth.User"  # Equivalent to ``model = myapp.models.User``
        django_get_or_create = ("username",)

    email = "testuser@example.com"
    username = "testuser"

    is_superuser = False
    is_staff = True
    is_active = True


def submit_order_of_pages(root_page, user, *, page_count: int = 1) -> wls_models.Order:
    """An order of freshly built pages, sent to Smartling the way an editor sends it."""
    order = OrderFactory(submitted_at=None, created_by=user)
    for index in range(page_count):
        page = InfoPageFactory(parent=root_page, title=f"{order.name} page {index + 1}")
        translation_source, _ = TranslationSource.get_or_create_from_instance(page)
        order_item = wls_models.OrderItem.objects.create(
            order=order, translation_source=translation_source, added_by=user
        )
        order_item.translations.set(
            Translation.objects.get_or_create(source=translation_source, target_locale=locale)[0]
            for locale in Locale.objects.exclude(pk=translation_source.locale_id)
        )
    order.submit(user=user)
    return order


def cancel_at_smartling(job: wls_models.Job) -> None:
    """
    Leave a job in the state a cancellation in Smartling leaves it in.

    A cancelled job has necessarily been pushed, and the sync-date constraint
    refuses a status without the dates and the uid that go with it.
    """
    now = timezone.now()
    job.status = JobStatus.CANCELLED
    job.translation_job_uid = job.translation_job_uid or f"cancelled-job-{job.pk}"
    job.first_synced_at = job.first_synced_at or now
    job.last_synced_at = now
    job.save(update_fields=["status", "translation_job_uid", "first_synced_at", "last_synced_at"])


def send_to_smartling(job: wls_models.Job, status: JobStatus = JobStatus.IN_PROGRESS) -> None:
    """
    Leave a job in the state a successful push leaves it in.

    The sync dates are what separates a job Smartling has from one that never
    got there, and the status constraint refuses a status without them.
    """
    now = timezone.now()
    job.status = status
    job.translation_job_uid = job.translation_job_uid or f"smartling-job-{job.pk}"
    job.first_synced_at = job.first_synced_at or now
    job.last_synced_at = now
    job.save(update_fields=["status", "translation_job_uid", "first_synced_at", "last_synced_at"])
