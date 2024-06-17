# Generated by Django 5.0.6 on 2024-05-26 13:12

from django.core.management import call_command
from django.db import migrations


def delete_default_homepage(apps, schema_editor):
    Page = apps.get_model("wagtailcore.Page")
    Page.objects.filter(depth__gt=1).delete()

    # I'm never happy about this, but it works
    call_command("fixtree")


class Migration(migrations.Migration):
    dependencies = [
        ("testapp", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(delete_default_homepage, migrations.RunPython.noop)
    ]
