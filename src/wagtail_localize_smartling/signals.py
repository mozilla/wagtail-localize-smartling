from django.dispatch import Signal


# Sent per single translation imported - fired every time
individual_translation_imported = Signal()

# Sent once per translation import run if all went well
# and at least one translation was imported
translation_import_successful = Signal()

# Sent when a job transitions from UNSYNCED to AWAITING_AUTHORIZATION
# (i.e. it has been created in Smartling and is awaiting authorization)
job_synced_to_smartling = Signal()
