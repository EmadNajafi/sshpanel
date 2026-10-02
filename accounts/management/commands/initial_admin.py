"""Create the first panel administrator from stdin, without a password argument."""
import re
import sys

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


ADMIN_USERNAME = re.compile(r"[A-Za-z0-9_]{1,150}\Z")


class Command(BaseCommand):
    help = "Create the initial web administrator from two stdin lines."

    def handle(self, *args, **options):
        username = sys.stdin.readline().rstrip("\n")
        password = sys.stdin.readline().rstrip("\n")
        if not ADMIN_USERNAME.fullmatch(username):
            raise CommandError("Administrator username must use English letters, digits or underscore (1-150 characters).")
        if not password or len(password) > 4096 or "\r" in password or "\x00" in password:
            raise CommandError("Administrator password must be nonempty (maximum 4096 characters).")
        user_model = get_user_model()
        if user_model.objects.filter(username=username).exists():
            raise CommandError("Administrator username already exists.")
        user_model.objects.create_superuser(username=username, email="", password=password)
        self.stdout.write(self.style.SUCCESS(f"Administrator {username} created."))
