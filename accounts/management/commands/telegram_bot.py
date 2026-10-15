from django.core.management.base import BaseCommand

from accounts.telegram_bot import run_polling


class Command(BaseCommand):
    help = "Run the private Telegram administration bot."

    def handle(self, *args, **options):
        run_polling()
