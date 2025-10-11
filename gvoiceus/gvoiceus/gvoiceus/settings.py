# gvoiceus/settings.py
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# -------- helpers to parse env --------
def env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    if v is None:
        return default
    # remove accidental wrapping quotes from .env values
    return v.strip().strip('"').strip("'")

def env_list(name: str, default_csv: str = "") -> list[str]:
    raw = env(name, default_csv) or ""
    return [x.strip() for x in raw.split(",") if x.strip()]

# ========= Core =========
SECRET_KEY = env("DJANGO_SECRET_KEY", "CHANGE-ME-IN-PROD")
DEBUG = (env("DEBUG", "True") or "True").lower() == "true"

# Hosts / CSRF
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "127.0.0.1,localhost")

# IMPORTANT: এখানে scheme সহ origin লাগবে (http/https + host + optional port)
CSRF_TRUSTED_ORIGINS = env_list(
    "CSRF_TRUSTED_ORIGINS",
    "http://127.0.0.1:8000,http://localhost:8000,https://127.0.0.1:8000,https://localhost:8000"
)

# Base URL (যদি কোথাও দরকার হয়)
SITE_URL = env("SITE_URL", "http://127.0.0.1:8000")

# ========= Apps =========
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # Useful for email templates (site_name context)
    "django.contrib.sites",

    # security / extras
    "axes",                     # brute-force lockout
    "django_otp",
    "django_otp.plugins.otp_totp",
    "corsheaders",

    "core",

    # লোকালে HTTPS চালাতে চাইলে
    "sslserver",

    "hcaptcha",
]
SITE_ID = int(env("SITE_ID", "1") or "1")

# ========= Middleware (order matters) =========
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",

    # Static files (works in dev & prod)
    "whitenoise.middleware.WhiteNoiseMiddleware",

    # CORS early
    "corsheaders.middleware.CorsMiddleware",

    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",

    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",

    "axes.middleware.AxesMiddleware",
]

ROOT_URLCONF = "gvoiceus.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # ✅ FIX: প্রজেক্ট-লেভেল টেমপ্লেট ফোল্ডার (gvoiceus/ নয়)
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.cart",   # navbar cart badge
            ],
        },
    },
]

WSGI_APPLICATION = "gvoiceus.wsgi.application"

# ========= Database =========
if env("DB_ENGINE"):
    DATABASES = {
        "default": {
            "ENGINE": env("DB_ENGINE"),
            "NAME": env("DB_NAME", ""),
            "USER": env("DB_USER", ""),
            "PASSWORD": env("DB_PASSWORD", ""),
            "HOST": env("DB_HOST", "localhost"),
            "PORT": env("DB_PORT", ""),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# ========= Auth / Passwords =========
# শক্তিশালী হ্যাশিং (Argon2 প্রথমে)
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": int(env("PASSWORD_MIN_LENGTH", "8") or "8")}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# পাসওয়ার্ড রিসেট টোকেনের মেয়াদ (seconds)
PASSWORD_RESET_TIMEOUT = int(env("PASSWORD_RESET_TIMEOUT", str(60 * 60 * 2)))  # 2 hours

AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "home"

# ========= i18n / tz =========
LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

# ========= Static / Media =========
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"  # for collectstatic in prod
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").exists() else []

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "whitenoise.storage.CompressedManifestStaticFilesStorage"
            if not DEBUG
            else "django.contrib.staticfiles.storage.StaticFilesStorage"
        )
    },
}

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ========= Email =========
# প্রোডাকশনে SMTP, লোকালে পাসওয়ার্ড সেট না থাকলে console backend
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = env("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(env("EMAIL_PORT", "587") or "587")
EMAIL_USE_TLS = (env("EMAIL_USE_TLS", "True") or "True").lower() == "true"
EMAIL_TIMEOUT = int(env("EMAIL_TIMEOUT", "20") or "20")

EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL = env(
    "DEFAULT_FROM_EMAIL",
    f"Gvoiceus <{EMAIL_HOST_USER}>" if EMAIL_HOST_USER else "no-reply@localhost"
)
SERVER_EMAIL = DEFAULT_FROM_EMAIL

# dev fallback: avoid SMTP errors if creds missing
if DEBUG and not EMAIL_HOST_PASSWORD:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# ইমেইল কনফার্ম (যদি প্রয়োজন হয়)
EMAIL_CONFIRM_MAX_AGE = int(env("EMAIL_CONFIRM_MAX_AGE", str(60 * 60 * 24)))  # 24h

# ========= django-axes (basic) =========
AXES_FAILURE_LIMIT = int(env("AXES_FAILURE_LIMIT", "5") or "5")
AXES_COOLOFF_TIME = int(env("AXES_COOLOFF_TIME", "1") or "1")  # hours
AXES_LOCKOUT_CALLABLE = None  # default DB-based tracking যথেষ্ট

# ========= CORS (optional) =========
CORS_ALLOW_ALL_ORIGINS = (env("CORS_ALLOW_ALL", "False") or "False").lower() == "true"

# ========= Security headers =========
if DEBUG:
    # Don’t force HTTPS in local dev
    SECURE_SSL_REDIRECT = False
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False
    # Dev static convenience
    WHITENOISE_USE_FINDERS = True
    WHITENOISE_AUTOREFRESH = True
else:
    SECURE_SSL_REDIRECT = (env("SECURE_SSL_REDIRECT", "True") or "True").lower() == "true"
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(env("SECURE_HSTS_SECONDS", "31536000") or "31536000")
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    # NOTE: Django 5-এ X-XSS-Protection হেডার ডিপ্রিকেটেড, তাই সেট না করলেও হবে
    X_FRAME_OPTIONS = "DENY"
    REFERRER_POLICY = "strict-origin-when-cross-origin"

# FX rates for SSLCommerz conversion (string/number—দুটোই চলবে)
FX_USD_BDT = os.environ.get("FX_USD_BDT", "125.00")
FX_EUR_BDT = os.environ.get("FX_EUR_BDT", "130.00")
FX_GBP_BDT = os.environ.get("FX_GBP_BDT", "140.00")

# ========= SSLCOMMERZ CONFIG =========
SSLC_STORE_ID = env("SSLC_STORE_ID")
SSLC_STORE_PASS = env("SSLC_STORE_PASS")   # ← PASSWD নয়, PASS
SSLC_SANDBOX = (env("SSLC_SANDBOX", "True") or "True").lower() == "true"

# ========= Customer defaults =========
CUSTOMER_DEFAULT_PHONE = env("CUSTOMER_DEFAULT_PHONE", "01700000000")

# ===== hCaptcha config =====
# তুমি .env এ দিয়েছে:
# HCAPTCHA_SITEKEY=5f227756-b456-48a4-9b22-8cc4d856a138
# HCAPTCHA_SECRET=ES_d9567ec5f4ea4f709c02bc999d76c401
HCAPTCHA_SITEKEY = os.environ.get("HCAPTCHA_SITEKEY", "10000000-ffff-ffff-ffff-000000000001")  # test sitekey (dev)
HCAPTCHA_SECRET = os.environ.get("HCAPTCHA_SECRET", "0x0000000000000000000000000000000000000000")  # test secret (dev)

# (optional) API endpoint override (সাধারণত দরকার হয় না)
HCAPTCHA_API_ENDPOINT = os.environ.get("HCAPTCHA_API_ENDPOINT", "https://hcaptcha.com/siteverify")
