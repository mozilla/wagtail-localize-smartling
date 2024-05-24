from django.core.management import BaseCommand


class Command(BaseCommand):
    """
    Management command intended to be run on a schedule (e.g. every 10 minutes) that:
    - Picks up any pending translation jobs that need to be sent to Smartling
    - Checks the status of any unfinalised jobs and updates them as appropriate
    - Applies any new translations
    """

    def handle(self, *args, **kwargs) -> None:
        # TODO
        pass
