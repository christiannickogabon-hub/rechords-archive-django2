"""Django settings for the Document Archiving System."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = os.getenv("DJANGO_DEBUG", "True").lower() == "true"
ALLOWED_HOSTS = [h for h in os.getenv("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if h]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "cloudinary_storage",
    "django.contrib.staticfiles",
    "cloudinary",
    "django.contrib.humanize",
    "accounts",
    "documents",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "accounts.context_processors.notifications",
                "accounts.context_processors.pending_account_requests",
                "documents.context.asset_version",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

if os.getenv("DB_NAME"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("DB_NAME"),
            "USER": os.getenv("DB_USER"),
            "PASSWORD": os.getenv("DB_PASSWORD"),
            "HOST": os.getenv("DB_HOST", "localhost"),
            "PORT": os.getenv("DB_PORT", "5432"),
        }
    }
else:
    # No DB_NAME set — fall back to sqlite so the dev server keeps working
    # until a Postgres server is actually available.
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "documents:dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Manila"
USE_I18N = True
USE_TZ = True

# Word files must pass through an office suite before they can be stamped.
# Leave blank and LibreOffice is looked for in the usual places; set it to the
# full path of soffice.exe to be explicit.
DOC_TO_PDF_COMMAND = os.getenv("DOC_TO_PDF_COMMAND", "")

# Documents stay in the Recycle Bin this long before they are offered for disposal.
# Nothing is ever destroyed automatically: a super administrator must approve it.
RECYCLE_BIN_RETENTION_DAYS = int(os.getenv("RECYCLE_BIN_RETENTION_DAYS", "30"))

# Brevo sends the one-time sign-in codes for super administrators.
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "")
BREVO_SENDER_EMAIL = os.getenv("BREVO_SENDER_EMAIL", "")
BREVO_SENDER_NAME = os.getenv("BREVO_SENDER_NAME", "Municipality of Carigara Document Archive")

# Bumped whenever app.css changes, so browsers never serve a stale stylesheet.
try:
    ASSET_VERSION = int((BASE_DIR / "static" / "css" / "app.css").stat().st_mtime)
except OSError:
    ASSET_VERSION = 0

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# Cloudinary: avatars, scanned documents and attachments are stored and
# served from Cloudinary instead of local disk (media/ is unused once this
# is configured). Non-image files (PDF/DOC/DOCX/CSV) use RawMediaCloudinaryStorage,
# set explicitly per-field in documents/models.py — the "image" resource type
# below only suits the avatar field.
CLOUDINARY_STORAGE = {
    "CLOUD_NAME": os.getenv("CLOUDINARY_CLOUD_NAME"),
    "API_KEY": os.getenv("CLOUDINARY_API_KEY"),
    "API_SECRET": os.getenv("CLOUDINARY_API_SECRET"),
}
STORAGES = {
    "default": {"BACKEND": "cloudinary_storage.storage.MediaCloudinaryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Uploads: 25 MB ceiling, whitelist enforced in documents.forms
MAX_UPLOAD_SIZE = 25 * 1024 * 1024
ALLOWED_UPLOAD_EXTENSIONS = [".pdf", ".doc", ".docx", ".jpg", ".jpeg", ".png", ".tif", ".tiff"]

# Bulk import: CSV rows cap enforced by the web importer (documents.importing.csv_engine).
# The `import_documents` management command can override it with --max-rows.
IMPORT_MAX_ROWS = 5000

# Bulk scan-attach posts one form field per staged PDF plus its resolution controls,
# so a batch of a few hundred files can easily clear Django's default 1000-field ceiling.
DATA_UPLOAD_MAX_NUMBER_FIELDS = 10000

MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"

if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
