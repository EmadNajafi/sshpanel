"""Create the first panel administrator from stdin, without a password argument."""
import sys

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create the initial web administrator from two stdin lines."

    def handle(self, *args, **options):
        username = sys.stdin.readline().strip()
        password = sys.stdin.readline().rstrip("\n")
        if not username or len(username) > 150 or any(not 32 <= ord(char) <= 126 for char in username):
            raise CommandError("Enter a nonempty English-keyboard username (maximum 150 characters).")
        if not password or len(password) > 4096 or "\r" in password or "\x00" in password:
            raise CommandError("Administrator password must be nonempty (maximum 4096 characters).")
        user_model = get_user_model()
        if user_model.objects.filter(username=username).exists():
            raise CommandError("Administrator username already exists.")
        user_model.objects.create_superuser(username=username, email="", password=password)
        self.stdout.write(self.style.SUCCESS(f"Administrator {username} created."))
