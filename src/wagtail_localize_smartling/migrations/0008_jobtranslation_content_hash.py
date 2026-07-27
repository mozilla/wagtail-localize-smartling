# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# Generated manually for issues #28 and #37

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("wagtail_localize_smartling", "0007_job_translations_through"),
    ]

    operations = [
        migrations.AddField(
            model_name="jobtranslation",
            name="content_hash",
            field=models.CharField(blank=True, editable=False, max_length=64),
        ),
    ]
