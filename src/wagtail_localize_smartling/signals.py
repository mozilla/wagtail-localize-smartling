# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.dispatch import Signal


# Sent per single translation imported - fired every time
individual_translation_imported = Signal()

# Sent once per translation import run if all went well
# and at least one translation was imported
translation_import_successful = Signal()
