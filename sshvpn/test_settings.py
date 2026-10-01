from .settings import *  # noqa: F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
WEB_PATH = ""
FORCE_SCRIPT_NAME = None
STATIC_URL = "/static/"
TLS_ENABLED = False
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
