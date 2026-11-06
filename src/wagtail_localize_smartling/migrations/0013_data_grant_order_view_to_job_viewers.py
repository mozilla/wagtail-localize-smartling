from django.db import migrations


def give_order_view_permission_to_job_viewers(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    order_content_type, _ = ContentType.objects.get_or_create(app_label="wagtail_localize_smartling", model="order")
    view_order, _ = Permission.objects.get_or_create(
        content_type=order_content_type,
        codename="view_order",
        defaults={"name": "Can view order"},
    )

    for group in Group.objects.filter(
        permissions__codename="view_job",
        permissions__content_type__app_label="wagtail_localize_smartling",
    ).distinct():
        group.permissions.add(view_order)


class Migration(migrations.Migration):
    dependencies = [
        ("wagtail_localize_smartling", "0012_job_sync_failure_fields"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        # The reverse does nothing on purpose, for the same reason as the grant
        # in 0011: it cannot tell what this migration gave from what somebody
        # granted by hand.
        migrations.RunPython(give_order_view_permission_to_job_viewers, migrations.RunPython.noop),
    ]
