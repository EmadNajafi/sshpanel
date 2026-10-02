import os
from pathlib import Path
import re


BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
DEBUG = False
TLS_ENABLED = os.environ.get("PANEL_TLS_ENABLED", "0") == "1"
ALLOWED_HOSTS = [os.environ["PANEL_DOMAIN"]]
HTTP_PORT = os.environ.get("PANEL_HTTP_PORT", "80")
HTTPS_PORT = os.environ.get("PANEL_HTTPS_PORT", "443")
WEB_PATH = os.environ.get("PANEL_WEB_PATH", "").rstrip("/")
if WEB_PATH and not re.fullmatch(r"/[A-Za-z0-9_-]{1,64}", WEB_PATH):
    raise ValueError("Invalid PANEL_WEB_PATH.")
FORCE_SCRIPT_NAME = WEB_PATH or None
VPN_SSH_PORT = int(os.environ.get("VPN_SSH_PORT", "22"))
if os.environ["PANEL_DOMAIN"] == "localhost":
    ALLOWED_HOSTS.append("127.0.0.1")
if TLS_ENABLED:
    https_port_suffix = "" if HTTPS_PORT == "443" else f":{HTTPS_PORT}"
    CSRF_TRUSTED_ORIGINS = [f"https://{os.environ['PANEL_DOMAIN']}{https_port_suffix}"]
else:
    port_suffix = "" if HTTP_PORT == "80" else f":{HTTP_PORT}"
    CSRF_TRUSTED_ORIGINS = [f"http://{host}{port_suffix}" for host in ALLOWED_HOSTS]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "sshvpn.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.debug",
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "sshvpn.wsgi.application"
DATABASES = {"default": {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": os.environ.get("DB_NAME", "sshvpn"),
    "USER": os.environ.get("DB_USER", "sshvpn"),
    "PASSWORD": os.environ["DB_PASSWORD"],
    "HOST": os.environ.get("DB_HOST", "127.0.0.1"),
    "PORT": os.environ.get("DB_PORT", "5432"),
    "CONN_MAX_AGE": 60,
}}
AUTH_PASSWORD_VALIDATORS = []
LANGUAGE_CODE = "en-us"
LANGUAGES = [("en", "English"), ("fa", "فارسی")]
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = f"{WEB_PATH}/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = TLS_ENABLED
SESSION_COOKIE_SECURE = TLS_ENABLED
CSRF_COOKIE_SECURE = TLS_ENABLED
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SECURE_HSTS_SECONDS = 31536000 if TLS_ENABLED else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_AGE = 3600
