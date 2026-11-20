from django.db import migrations


ORDER_PERMISSIONS = [
    ("add_order", "Can add order"),
    ("change_order", "Can change order"),
    ("delete_order", "Can delete order"),
    ("view_order", "Can view order"),
]


def give_order_permissions_to_submit_translation_groups(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    content_type, _ = ContentType.objects.get_or_create(app_label="wagtail_localize_smartling", model="order")
    order_permissions = [
        Permission.objects.get_or_create(content_type=content_type, codename=codename, defaults={"name": name})[0]
        for codename, name in ORDER_PERMISSIONS
    ]

    for group in Group.objects.filter(
        permissions__codename="submit_translation",
        permissions__content_type__app_label="wagtail_localize",
    ).distinct():
        group.permissions.add(*order_permissions)


class Migration(migrations.Migration):
    dependencies = [
        ("wagtail_localize_smartling", "0010_populate_orders"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
        # The permission this grant keys off is itself created by a migration,
        # which has to have run or the group filter matches nothing.
        ("wagtail_localize", "0006_create_submit_translation_permission"),
    ]

    operations = [
        # The reverse does nothing on purpose. Removing permissions would
        # remove them from both the Groups that received them in this migration's
        # forward function, and also from Groups that somebody granted them to
        # by hand.
        migrations.RunPython(give_order_permissions_to_submit_translation_groups, migrations.RunPython.noop),
    ]
