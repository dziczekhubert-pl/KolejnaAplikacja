"""
Django settings for kolejna_aplikacja project.

Dostosowane pod Render.com (Gunicorn + WhiteNoise + DATABASE_URL).
"""

from pathlib import Path
import os
from urllib.parse import urlparse

import dj_database_url  # <-- DODANE

# --------------------------------------------------------------------------------------
# ŚCIEŻKI
# --------------------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------------------
# KLUCZE / DEBUG / HOSTY
# --------------------------------------------------------------------------------------
SECRET_KEY = os.getenv(
    "DJANGO_SECRET_KEY",
    # dev only
    "django-insecure-evt(poz%^0@3!hgkq(qxc3+vbjti+zo3$c@b(p$yb!l=zekzej",
)
DEBUG = os.getenv("DJANGO_DEBUG", "1") == "1"

# ALLOWED_HOSTS: w DEV "*" (jak było), w prod czytamy listę z ENV
if DEBUG:
    ALLOWED_HOSTS = ["*"]
else:
    # Przykład ENV: DJANGO_ALLOWED_HOSTS="moja-app.onrender.com,www.example.com"
    _hosts_env = os.getenv("DJANGO_ALLOWED_HOSTS", "")
    ALLOWED_HOSTS = [h.strip() for h in _hosts_env.split(",") if h.strip()]

# CSRF_TRUSTED_ORIGINS: lista URL bazowych (https://...)
# Przykład ENV: DJANGO_CSRF_TRUSTED_ORIGINS="https://moja-app.onrender.com,https://www.example.com"
_csrf_env = os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "")
CSRF_TRUSTED_ORIGINS = [o.strip() for o in _csrf_env.split(",") if o.strip()]

# (Opcjonalnie) jeżeli chcesz automatycznie zaufać subdomenom Render:
# Dodaj je tylko gdy nie jesteś w DEBUG i nie masz jeszcze nic ustawionego.
if not DEBUG and not CSRF_TRUSTED_ORIGINS:
    # Uwaga: Render używa domen w stylu https://twoja-usluga.onrender.com
    # Jeśli znasz swój host, najlepiej ustaw go jawnie w ENV zamiast wildcardów.
    pass  # zostaw puste lub ustaw jawnie w ENV

# --------------------------------------------------------------------------------------
# APLIKACJE
# --------------------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # Twoja aplikacja
    "core.apps.CoreConfig",
]

# --------------------------------------------------------------------------------------
# MIDDLEWARE
# --------------------------------------------------------------------------------------
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # WhiteNoise powinien być tuż po SecurityMiddleware:
    "whitenoise.middleware.WhiteNoiseMiddleware",  # <-- DODANE
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# --------------------------------------------------------------------------------------
# URL / WSGI
# --------------------------------------------------------------------------------------
ROOT_URLCONF = "kolejna_aplikacja.urls"
WSGI_APPLICATION = "kolejna_aplikacja.wsgi.application"

# --------------------------------------------------------------------------------------
# TEMPLATES
# --------------------------------------------------------------------------------------
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# --------------------------------------------------------------------------------------
# BAZA DANYCH (Render → DATABASE_URL)
# --------------------------------------------------------------------------------------
# Jeśli ustawisz DATABASE_URL (np. postgresql://...), użyjemy go.
# W DEV fallback do sqlite3.
DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=int(os.getenv("POSTGRES_CONN_MAX_AGE", "60")),
            ssl_require=True,  # Render Postgres ma TLS – wymuś SSL w produkcji
        )
    }
else:
    # DEV fallback – pojedynczy plik
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# --------------------------------------------------------------------------------------
# MIĘDZYNARODOWOŚĆ / CZAS
# --------------------------------------------------------------------------------------
LANGUAGE_CODE = "pl"
TIME_ZONE = "Europe/Warsaw"
USE_I18N = True
USE_TZ = True

# --------------------------------------------------------------------------------------
# STATIC / MEDIA
# --------------------------------------------------------------------------------------
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").exists() else []
STATIC_ROOT = BASE_DIR / "staticfiles"

# WhiteNoise – kompresja i hash nazw plików statycznych:
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"  # <-- DODANE

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# --------------------------------------------------------------------------------------
# DOMYŚLNY TYP PK
# --------------------------------------------------------------------------------------
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------------------
# LOGOWANIE
# --------------------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "[{levelname}] {asctime} {name} | {message}", "style": "{"},
        "simple": {"format": "{levelname}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
    },
    "loggers": {
        "django.db.backends": {
            "handlers": ["console"],
            "level": "INFO" if DEBUG else "WARNING",
        },
        "core": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}

# --------------------------------------------------------------------------------------
# BEZPIECZEŃSTWO (tylko gdy DEBUG = False)
# --------------------------------------------------------------------------------------
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = os.getenv("DJANGO_SECURE_SSL_REDIRECT", "1") == "1"
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

    SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = os.getenv("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", "1") == "1"
    SECURE_HSTS_PRELOAD = os.getenv("DJANGO_SECURE_HSTS_PRELOAD", "1") == "1"
    X_FRAME_OPTIONS = "DENY"

# --------------------------------------------------------------------------------------
# ŚCIEŻKI NA SNAPSHOTY JSON (jeśli korzystasz z eksportu do JSON)
# --------------------------------------------------------------------------------------
JSON_DATA_DIR = BASE_DIR / "data_json"
# Render ma ephemeral filesystem – katalog będzie kasowany po restarcie dyno.
# Jeśli tego potrzebujesz trwałe, rozważ S3/Wasabi/Backblaze.
JSON_DATA_DIR.mkdir(parents=True, exist_ok=True)

JSON_FILES = {
    "carts": JSON_DATA_DIR / "carts.json",
    "loads": JSON_DATA_DIR / "loads.json",
    "history": JSON_DATA_DIR / "history.jsonl",
}

# --------------------------------------------------------------------------------------
# (Opcjonalnie) CELERY – jeśli planujesz eksport snapshotów w tle
# --------------------------------------------------------------------------------------
# CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
# CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://127.0.0.1:6379/0")
# CELERY_TIMEZONE = TIME_ZONE
# CELERY_TASK_ALWAYS_EAGER = False
