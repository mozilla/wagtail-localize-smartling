import uuid

from datetime import UTC, datetime

import pytest

from django.conf import settings
from django.utils import timezone


# Every test here requests django_db_serialized_rollback. Applying a migration
# drops and rebuilds the schema, and the transactional teardown that follows
# flushes it; without the serialized snapshot, rows that earlier migrations
# created never come back, and the autouse locales fixture in conftest.py then
# fails in the setup of the next test in this file.
PRE_ORDER_MODELS_MIGRATION = ("wagtail_localize_smartling", "0008_jobtranslation_content_hash")
PRE_ORDERS_MIGRATION = ("wagtail_localize_smartling", "0009_job_sync_started_at_alter_job_translation_source_and_more")
POPULATE_ORDERS_MIGRATION = ("wagtail_localize_smartling", "0010_populate_orders")
GRANT_ORDER_PERMISSIONS_MIGRATION = ("wagtail_localize_smartling", "0011_data_grant_order_permissions")
GRANT_ORDER_VIEW_MIGRATION = ("wagtail_localize_smartling", "0013_data_grant_order_view_to_job_viewers")


def _build_pre_orders_job(state, **overrides):
    """
    Create a Job of the shape this package produced before Orders existed.

    Everything is built from the historical models, because that is what the
    migration will see: the real models have a custom save(), properties and a
    JobStatus enum that the migration cannot reach.
    """
    apps = state.apps
    Locale = apps.get_model("wagtailcore", "Locale")
    ContentType = apps.get_model("contenttypes", "ContentType")
    TranslatableObject = apps.get_model("wagtail_localize", "TranslatableObject")
    TranslationSource = apps.get_model("wagtail_localize", "TranslationSource")
    Translation = apps.get_model("wagtail_localize", "Translation")
    Project = apps.get_model("wagtail_localize_smartling", "Project")
    Job = apps.get_model("wagtail_localize_smartling", "Job")
    User = apps.get_model(settings.AUTH_USER_MODEL)

    content_type, _ = ContentType.objects.get_or_create(app_label="testapp", model="infopage")
    translatable_object = TranslatableObject.objects.create(
        translation_key=uuid.uuid4(),
        content_type=content_type,
    )
    translation_source = TranslationSource.objects.create(
        object=translatable_object,
        specific_content_type=content_type,
        locale=Locale.objects.get(language_code="en"),
        object_repr="A page from before orders existed",
        content_json="{}",
        schema_version="",
        last_updated_at=timezone.now(),
    )
    # The target locales are created here rather than taken from a fixture,
    # because applying the initial migration drops every table: only rows a
    # migration itself writes survive into the test.
    translations = [
        Translation.objects.create(
            source=translation_source,
            target_locale=Locale.objects.get_or_create(language_code=language_code)[0],
        )
        for language_code in ("fr", "de")
    ]

    project, _ = Project.objects.get_or_create(
        project_id="dummy_project_id",
        defaults={
            "environment": "production",
            "account_uid": "dummy_account_uid",
            "archived": False,
            "name": "Dummy project",
            "type_code": "APPLICATION_RESOURCES",
            "source_locale_description": "English (United States) [en-US]",
            "source_locale_id": "en-US",
        },
    )
    user, _ = User.objects.get_or_create(username="editor")

    job_kwargs = {
        "project": project,
        "user": user,
        "translation_source": translation_source,
        "name": "A page from before orders existed",
        "description": "",
        "reference_number": "ref-1",
        "due_date": None,
        "status": "UNSYNCED",
        "translation_job_uid": "",
        "file_uri": "job_1_ts_1.po",
        "content_hash": "abc",
        "first_synced_at": None,
        "last_synced_at": None,
    }
    job_kwargs.update(overrides)
    if job_kwargs["first_synced_at"] is not None:
        # status_consistent_with_sync_dates rejects a job that has been synced
        # but still looks unsynced, so a sync date brings the rest of that
        # shape with it.
        if job_kwargs["status"] == "UNSYNCED":
            job_kwargs["status"] = "DRAFT"
        if job_kwargs["last_synced_at"] is None:
            job_kwargs["last_synced_at"] = job_kwargs["first_synced_at"]
        if not job_kwargs["translation_job_uid"]:
            job_kwargs["translation_job_uid"] = "job_uid_1"
    job = Job.objects.create(**job_kwargs)
    job.translations.set(translations)

    return job


@pytest.mark.django_db
def test_backfill_gives_every_eligible_job_its_own_order(migrator, django_db_serialized_rollback, settings):
    settings.WAGTAIL_LOCALIZE_SMARTLING = {
        **settings.WAGTAIL_LOCALIZE_SMARTLING,
        "JOB_NAME_PREFIX": "PREFIX",
    }
    old_state = migrator.apply_initial_migration(PRE_ORDERS_MIGRATION)
    Order = old_state.apps.get_model("wagtail_localize_smartling", "Order")
    OrderItem = old_state.apps.get_model("wagtail_localize_smartling", "OrderItem")
    JobFile = old_state.apps.get_model("wagtail_localize_smartling", "JobFile")
    hand_made_order = Order.objects.create(name="Assembled by hand", reference_number="kept")

    first_job = _build_pre_orders_job(old_state)
    second_job = _build_pre_orders_job(old_state, name="A second page", file_uri="job_2_ts_2.po")
    prefixed_job = _build_pre_orders_job(old_state, name=f"PREFIX {'x' * 150}", reference_number="")
    already_ordered_job = _build_pre_orders_job(old_state, order=hand_made_order)
    sourceless_job = _build_pre_orders_job(old_state, translation_source=None)

    # There is currently 1 Order in the database, 0 OrderItems, and 0 JobFiles.
    assert Order.objects.count() == 1
    assert OrderItem.objects.count() == 0
    assert JobFile.objects.count() == 0

    new_state = migrator.apply_tested_migration(POPULATE_ORDERS_MIGRATION)

    Job = new_state.apps.get_model("wagtail_localize_smartling", "Job")
    Order = new_state.apps.get_model("wagtail_localize_smartling", "Order")
    OrderItem = new_state.apps.get_model("wagtail_localize_smartling", "OrderItem")
    JobFile = new_state.apps.get_model("wagtail_localize_smartling", "JobFile")
    # Three of the five jobs are eligible, and the hand-made order is a fourth
    # that the backfill must not touch. Counting the totals here is what would
    # catch a stray row that the per-job assertions below would walk past.
    assert Order.objects.count() == 4
    assert OrderItem.objects.count() == 3
    assert JobFile.objects.count() == 3

    for migrated_job_pk, expected_file_uri in [(first_job.pk, "job_1_ts_1.po"), (second_job.pk, "job_2_ts_2.po")]:
        migrated_job = Job.objects.get(pk=migrated_job_pk)
        assert migrated_job.order is not None
        assert migrated_job.order.name == migrated_job.name
        assert migrated_job.order.reference_number == migrated_job.reference_number
        assert migrated_job.order.submitted_at is not None
        assert list(migrated_job.order.contributors.values_list("pk", flat=True)) == [migrated_job.user_id]
        assert migrated_job.files.get().translation_source_id == migrated_job.translation_source_id
        assert migrated_job.files.get().file_uri == expected_file_uri

        order_item = migrated_job.order.order_items.get()
        assert order_item.translation_source_id == migrated_job.translation_source_id
        assert set(order_item.translations.values_list("pk", flat=True)) == set(
            migrated_job.translations.values_list("pk", flat=True)
        )

    # The two jobs must not have been folded into one order between them.
    migrated_jobs_order_ids = Job.objects.filter(pk__in=[first_job.pk, second_job.pk]).values_list(
        "order_id", flat=True
    )
    assert len(set(migrated_jobs_order_ids)) == 2
    migrated_second_job = Job.objects.get(pk=second_job.pk)
    assert migrated_second_job.order.name == "A second page"

    # The Order with the prefixed Job does not have the prefix in the Order's name.
    prefixed_order = Job.objects.get(pk=prefixed_job.pk).order
    assert prefixed_order.name == "x" * 100
    assert len(prefixed_order.reference_number) == 32

    # A job already belonging to an order kept it, and gained no file.
    assert Job.objects.get(pk=already_ordered_job.pk).order_id == hand_made_order.pk
    assert Order.objects.get(pk=hand_made_order.pk).reference_number == "kept"
    assert not JobFile.objects.filter(job_id=already_ordered_job.pk).exists()

    # OrderItem and JobFile both require a source, so this job was not given an Order.
    assert Job.objects.get(pk=sourceless_job.pk).order_id is None


@pytest.mark.django_db
def test_backfill_prefers_first_synced_at(migrator, django_db_serialized_rollback):
    """The backfill sets Order.submitted_at from the old Job's first_synced_at value."""
    sent_on = datetime(2025, 1, 2, tzinfo=UTC)
    old_state = migrator.apply_initial_migration(PRE_ORDERS_MIGRATION)
    job = _build_pre_orders_job(old_state, first_synced_at=sent_on)

    new_state = migrator.apply_tested_migration(POPULATE_ORDERS_MIGRATION)

    Job = new_state.apps.get_model("wagtail_localize_smartling", "Job")
    order = Job.objects.get(pk=job.pk).order
    assert order.submitted_at == sent_on
    assert order.created_at == sent_on


@pytest.mark.django_db
def test_reversing_the_backfill_keeps_what_it_created(migrator, django_db_serialized_rollback):
    old_state = migrator.apply_initial_migration(PRE_ORDERS_MIGRATION)
    _build_pre_orders_job(old_state)

    migrator.apply_tested_migration(POPULATE_ORDERS_MIGRATION)
    back = migrator.apply_tested_migration(PRE_ORDERS_MIGRATION)

    Order = back.apps.get_model("wagtail_localize_smartling", "Order")
    JobFile = back.apps.get_model("wagtail_localize_smartling", "JobFile")
    Job = back.apps.get_model("wagtail_localize_smartling", "Job")
    assert Order.objects.count() == 1
    assert JobFile.objects.count() == 1
    assert Job.objects.get().order_id is not None

    # Re-applying must not wrap the same job a second time.
    forward = migrator.apply_tested_migration(POPULATE_ORDERS_MIGRATION)
    Order = forward.apps.get_model("wagtail_localize_smartling", "Order")
    Job = forward.apps.get_model("wagtail_localize_smartling", "Job")
    assert Order.objects.count() == 1
    assert Job.objects.get().files.count() == 1


@pytest.mark.django_db
def test_groups_that_can_submit_translations_can_manage_orders(migrator, django_db_serialized_rollback):
    """Groups that submit translations are given permissions to use Orders."""
    old_state = migrator.apply_initial_migration(PRE_ORDER_MODELS_MIGRATION)
    Group = old_state.apps.get_model("auth", "Group")
    Permission = old_state.apps.get_model("auth", "Permission")
    submit_translation_permission = Permission.objects.get(
        codename="submit_translation", content_type__app_label="wagtail_localize"
    )
    translators = Group.objects.create(name="Translators")
    # Translators have the "submit_translation" permission.
    translators.permissions.add(submit_translation_permission)
    # Bystanders have no permissions.
    Group.objects.create(name="Bystanders")

    new_state = migrator.apply_tested_migration(GRANT_ORDER_PERMISSIONS_MIGRATION)

    Group = new_state.apps.get_model("auth", "Group")
    translator_permissions = Group.objects.get(name="Translators").permissions
    # The names are asserted alongside the codenames because Django only ever
    # writes a permission's name when it creates the row, so a wrong one here
    # would stay wrong on every site that upgrades.
    permissions_granted_to_translators = set(
        translator_permissions.filter(
            content_type__app_label="wagtail_localize_smartling",
            content_type__model="order",
        ).values_list("codename", "name")
    )
    assert permissions_granted_to_translators == {
        ("add_order", "Can add order"),
        ("change_order", "Can change order"),
        ("delete_order", "Can delete order"),
        ("view_order", "Can view order"),
    }
    # Translators still have the permissions they had before the
    # GRANT_ORDER_PERMISSIONS_MIGRATION.
    assert translator_permissions.filter(codename="submit_translation").exists()
    # Bystanders still have no permissions.
    assert not Group.objects.get(name="Bystanders").permissions.exists()


@pytest.mark.django_db
def test_groups_that_could_read_the_jobs_can_read_the_orders(migrator, django_db_serialized_rollback):
    """
    Users who had "view_job" permission now have "view_order" permission.

    The report at /smartling-jobs/ used to list jobs (and needed the "view_job"
    permisison), but now lists orders (and now needs to "view_order" permisison).
    """
    old_state = migrator.apply_initial_migration(PRE_ORDER_MODELS_MIGRATION)
    ContentType = old_state.apps.get_model("contenttypes", "ContentType")
    Group = old_state.apps.get_model("auth", "Group")
    Permission = old_state.apps.get_model("auth", "Permission")
    # Django writes this one from a post_migrate signal that rolling back to an
    # earlier migration does not replay, so the test writes it itself.
    job_content_type, _ = ContentType.objects.get_or_create(app_label="wagtail_localize_smartling", model="job")
    view_job, _ = Permission.objects.get_or_create(
        content_type=job_content_type, codename="view_job", defaults={"name": "Can view job"}
    )
    reporters = Group.objects.create(name="Reporters")
    reporters.permissions.add(view_job)
    # Bystanders have no permissions.
    Group.objects.create(name="Bystanders")

    new_state = migrator.apply_tested_migration(GRANT_ORDER_VIEW_MIGRATION)

    Group = new_state.apps.get_model("auth", "Group")
    reporter_permissions = Group.objects.get(name="Reporters").permissions
    assert set(
        reporter_permissions.filter(
            content_type__app_label="wagtail_localize_smartling",
            content_type__model="order",
        ).values_list("codename", "name")
    ) == {("view_order", "Can view order")}
    # Reading the report is all they could do before, and all they can do now.
    assert reporter_permissions.filter(codename="view_job").exists()
    assert not Group.objects.get(name="Bystanders").permissions.exists()
