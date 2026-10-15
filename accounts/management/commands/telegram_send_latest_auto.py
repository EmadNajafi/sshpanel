from django.core.management.base import BaseCommand, CommandError

from accounts.telegram_api import TelegramError
from accounts.telegram_bot import send_latest_automatic_backup


class Command(BaseCommand):
    help = "Send the newest automatic backup to the configured Telegram administrators."

    def handle(self, *args, **options):
        try:
            self.stdout.write(send_latest_automatic_backup())
        except (OSError, TelegramError) as exc:
            raise CommandError(str(exc)) from None
