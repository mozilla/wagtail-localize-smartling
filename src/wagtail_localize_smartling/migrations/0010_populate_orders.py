import uuid

from django.conf import settings
from django.db import migrations
from django.utils import timezone


def _get_job_name_prefix() -> str:
    """The configured Smartling job name prefix, read without importing the app."""
    return (getattr(settings, "WAGTAIL_LOCALIZE_SMARTLING", None) or {}).get("JOB_NAME_PREFIX") or ""


def populate_orders(apps, schema_editor):
    """Populate Orders (and other related models) from Jobs."""
    Job = apps.get_model("wagtail_localize_smartling", "Job")
    Order = apps.get_model("wagtail_localize_smartling", "Order")
    OrderItem = apps.get_model("wagtail_localize_smartling", "OrderItem")
    JobFile = apps.get_model("wagtail_localize_smartling", "JobFile")

    # An existing UNSYNCED job is a pending submission, not an abandoned one.
    # We don't have a creation time for the job, so we fall back to this value.
    # The value is only compared against NULL, so approximate is enough; unset
    # would be wrong.
    now = timezone.now()
    prefix = _get_job_name_prefix()

    # A job with no translation source cannot become an order, because OrderItem
    # and JobFile both require one. Reading the rows up front keeps the loop from
    # writing to the order column it is still scanning.
    jobs = list(Job.objects.filter(order__isnull=True, translation_source__isnull=False))

    for job in jobs:
        name = job.name
        # Order.suggested_job_name() puts the prefix back on every send, so a
        # name that kept it would carry it twice on the first resend.
        if prefix and name.startswith(f"{prefix} "):
            name = name[len(prefix) + 1 :]

        submitted_at = job.first_synced_at or now
        order = Order.objects.create(
            # Job.name allows more characters than Order.name, so a job named
            # at full length would fail the backfill rather than migrate.
            name=name[:100],
            due_date=job.due_date,
            created_by_id=job.user_id,
            # A job synced before Smartling returned a reference number has
            # none to copy. Order.save() would set one the next time the order
            # is saved anyway, so it is created here, where it is visible.
            reference_number=job.reference_number or uuid.uuid4().hex,
            submitted_at=submitted_at,
        )
        # created_at is auto_now_add, so creating the row stamps it with the
        # migration's run time, and the listing, ordered by -created_at, would
        # show every historical order in whatever order this loop ran.
        Order.objects.filter(pk=order.pk).update(created_at=submitted_at)
        order.contributors.add(job.user_id)

        order_item = OrderItem.objects.create(
            order=order,
            translation_source_id=job.translation_source_id,
            added_by_id=job.user_id,
        )
        order_item.translations.set(job.translations.all())
        JobFile.objects.create(
            job=job,
            translation_source_id=job.translation_source_id,
            file_uri=job.file_uri,
            content_hash=job.content_hash,
        )
        Job.objects.filter(pk=job.pk).update(order=order)


class Migration(migrations.Migration):
    dependencies = [
        ("wagtail_localize_smartling", "0009_job_sync_started_at_alter_job_translation_source_and_more"),
    ]

    operations = [
        # The reverse does nothing on purpose. Nothing marks an Order as one
        # this migration created, so undoing it would mean deleting every
        # Order, including the multi-page ones editors made afterwards, which
        # no second forward pass could rebuild. Leaving the rows alone is safe
        # in both directions: the forward pass skips jobs that already have an
        # order, so re-applying it is a no-op rather than a duplicate.
        migrations.RunPython(populate_orders, migrations.RunPython.noop),
    ]
