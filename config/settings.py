"""
Chicano Cruise API: Django settings.

Architecture (what this project is and is not):

    React Native Expo apps (4)  ──HTTP/JSON──►  Django REST Framework (/api/v1/)  ──►  PostgreSQL
    Staff in a browser          ──HTML──────►  Django templates      (/dashboard/)  ──►  (same database)

* For the mobile apps Django is a *standalone REST API*: JSON only, no mobile UI.
* The Super Admin web dashboard (apps.dashboard) is plain server-rendered Django:
  templates + HTML/CSS + a little vanilla JavaScript, session login for staff.
* Developer tooling:
    - /api/docs/     Swagger UI   (interactive API docs)
    - /api/redoc/    ReDoc        (readable API reference)
    - /django-admin/ Django's built-in admin (engineering use only, never for ops staff)
* One combined database holds users (customers), drivers (cars) and riders (bikes).
  A single `accounts.User` row can be a customer, a provider, staff, or several at once.

All configuration comes from environment variables so the same code runs locally,
in Docker and in production. See `.env.example` for every variable.
"""
from datetime import timedelta
from pathlib import Path
import os
from dotenv import load_dotenv
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

def env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean environment variable ("1", "true", "yes" count as True)."""
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    """Read a comma-separated environment variable into a list."""
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


# --------------------------------------------------------------------------- core
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me-in-production")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,0.0.0.0,10.0.2.2")

INSTALLED_APPS = [
    # Django
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third party
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",  # lets /auth/logout/ revoke refresh tokens
    "drf_spectacular",                            # OpenAPI 3 schema + Swagger UI + ReDoc
    "django_filters",
    "corsheaders",
    # Chicano Cruise apps (one Django app per business domain)
    "apps.core",       # shared base models, error format, permissions, audit log
    "apps.accounts",   # User (combined for everyone), OTP login, devices, staff roles
    "apps.customers",  # customer profile, saved places, emergency contacts
    "apps.providers",  # drivers (cars) + riders (bikes): profile, vehicles, documents, location
    "apps.pricing",    # ride types, fare rules, service zones, fare quotes
    "apps.rides",      # rides, stops, offers (dispatch), events, ratings
    "apps.payments",   # payment methods, wallet, promos, provider earnings + payouts
    "apps.support",    # tickets, lost items, SOS alerts, notifications
    "apps.staff",      # Super Admin business actions + optional JSON API (no models of its own)
    "apps.dashboard",  # Super Admin web dashboard: Django templates, HTML/CSS/JS (no models)
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",  # must be high up so preflight requests work
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # serves the dashboard's CSS/JS in production
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        # Used by the Super Admin dashboard (apps/dashboard/templates), Django admin and Swagger/ReDoc.
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.dashboard.context_processors.dashboard",   # menu for the staff role, service switch, SOS count
            ],
        },
    },
]

# --------------------------------------------------------------------------- database
# PostgreSQL is the production database. DATABASE_URL format:
#   postgres://USER:PASSWORD@HOST:5432/DBNAME
# (For a quick throwaway run you may set DATABASE_URL=sqlite:///db.sqlite3.)
DATABASES = {
    "default": dj_database_url.config(
        default=os.environ.get("DATABASE_URL", "postgres://chicano:chicano@localhost:5432/chicano"),
        conn_max_age=60,
    )
}

AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "dashboard:login"            # staff dashboard sign-in (mobile apps use OTP + JWT instead)
SESSION_COOKIE_AGE = 60 * 60 * 12        # dashboard sessions last one working day
SESSION_COOKIE_SECURE = not DEBUG        # HTTPS-only cookies in production
CSRF_COOKIE_SECURE = not DEBUG
# The dashboard's forms post from these origins when served over HTTPS behind a domain,
# e.g. CSRF_TRUSTED_ORIGINS=https://admin.chicanocruise.com
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", "")
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

# --------------------------------------------------------------------------- i18n / time
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Lagos"
USE_I18N = True
USE_TZ = True  # all datetimes in the API are ISO 8601 with offset

# --------------------------------------------------------------------------- files
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"     # `collectstatic` target; WhiteNoise serves it in production
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"  # document uploads; use S3-compatible storage in production

# --------------------------------------------------------------------------- CORS
# The Expo app on a phone does not need CORS (it is not a browser), but Expo Web and
# the Super Admin web app do. List their origins here.
CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", "http://localhost:8081,http://localhost:19006")

# --------------------------------------------------------------------------- DRF
REST_FRAMEWORK = {
    # Every endpoint requires a JWT unless the view says otherwise (e.g. OTP request).
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework_simplejwt.authentication.JWTAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    # JSON in, JSON out. Multipart is allowed for document/photo uploads.
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.MultiPartParser",
        "rest_framework.parsers.FormParser",
    ],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.StandardPagination",
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    # One error shape for the whole API. See apps/core/exceptions.py.
    "EXCEPTION_HANDLER": "apps.core.exceptions.api_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # Basic abuse protection; OTP endpoints add their own stricter scope.
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.ScopedRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"otp": "5/min", "location": "120/min"},
}

# --------------------------------------------------------------------------- JWT
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=int(os.environ.get("JWT_ACCESS_MINUTES", "30"))),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=int(os.environ.get("JWT_REFRESH_DAYS", "30"))),
    "ROTATE_REFRESH_TOKENS": True,        # each refresh returns a new refresh token
    "BLACKLIST_AFTER_ROTATION": True,     # the old one stops working
    "AUTH_HEADER_TYPES": ("Bearer",),     # header: Authorization: Bearer <access>
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

# --------------------------------------------------------------------------- OpenAPI docs
SPECTACULAR_SETTINGS = {
    "TITLE": "Chicano Cruise API",
    "DESCRIPTION": (
        "REST API for the four Chicano Cruise mobile apps (User app · Cars, Driver app · Cars, "
        "User app · Bikes, Rider app · Bikes) and the Super Admin web app.\n\n"
        "**Auth:** send `Authorization: Bearer <access_token>`. Get tokens from "
        "`POST /api/v1/auth/otp/verify/` (mobile) or `POST /api/v1/staff/auth/login/` (admin).\n\n"
        "**Money:** every amount is an integer in **kobo** (₦1 = 100 kobo).\n\n"
        "**Errors:** every error uses the same envelope: "
        "`{\"error\": {\"code\": \"...\", \"message\": \"...\", \"details\": {...}}}`."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,  # separate request/response schemas (better for codegen)
    "SCHEMA_PATH_PREFIX": r"/api/v1",
    "TAGS": [
        {"name": "Auth", "description": "Phone OTP login, tokens, current user, push devices. Used by all four apps."},
        {"name": "Customer", "description": "User apps (cars + bikes): profile, saved places, emergency contacts."},
        {"name": "Booking", "description": "User apps: ride types, fare estimates, requesting and tracking rides."},
        {"name": "Wallet & payments", "description": "User apps: wallet, payment methods, promo codes."},
        {"name": "Provider", "description": "Driver app (cars) + Rider app (bikes): onboarding, vehicle, documents, availability."},
        {"name": "Provider trips", "description": "Driver/Rider apps: offers, accept/decline, trip lifecycle."},
        {"name": "Provider earnings", "description": "Driver/Rider apps: earnings, payouts, incentives."},
        {"name": "Support & safety", "description": "Tickets, lost items, SOS, notifications, trip sharing."},
        {"name": "Staff", "description": "Super Admin web app only. Requires a staff account."},
    ],
    # Clear enum names in Swagger / generated TypeScript types instead of Status345Enum etc.
    "ENUM_NAME_OVERRIDES": {
        "ServiceEnum": "apps.core.models.Service",
        "UserStatusEnum": "apps.accounts.models.UserStatus",
        "AppKindEnum": "apps.accounts.models.AppKind",
        "DevicePlatformEnum": [("ios", "iOS"), ("android", "Android")],
        "WalletTxKindEnum": "apps.payments.models.WalletTxKind",
        "LedgerKindEnum": "apps.payments.models.LedgerKind",
        "PaymentPurposeEnum": "apps.payments.models.PaymentPurpose",
        "PaymentRecordStatusEnum": [("succeeded", "Succeeded"), ("failed", "Failed")],
        "DiscountTypeEnum": "apps.payments.models.DiscountType",
        "PayoutStatusEnum": "apps.payments.models.PayoutStatus",
        "PayoutMethodEnum": [("scheduled", "Weekly"), ("instant", "Instant")],
        "ProviderStatusEnum": "apps.providers.models.ProviderStatus",
        "VehicleStatusEnum": "apps.providers.models.VehicleStatus",
        "DocumentTypeEnum": "apps.providers.models.DocumentType",
        "DocumentStatusEnum": "apps.providers.models.DocumentStatus",
        "RideStatusEnum": "apps.rides.models.RideStatus",
        "PaymentMethodKindEnum": "apps.rides.models.PaymentMethodKind",
        "RidePaymentStatusEnum": "apps.rides.models.PaymentStatus",
        "CancelledByEnum": "apps.rides.models.CancelledBy",
        "OfferStatusEnum": "apps.rides.models.OfferStatus",
        "TicketStatusEnum": "apps.support.models.TicketStatus",
        "TicketCategoryEnum": "apps.support.models.TicketCategory",
        "TicketPriorityEnum": [("low", "Low"), ("normal", "Normal"), ("high", "High"), ("urgent", "Urgent")],
        "LostItemStatusEnum": "apps.support.models.LostItemStatus",
        "LostItemCategoryEnum": [("phone", "Phone"), ("bag", "Bag"), ("wallet_id", "Wallet / ID"), ("other", "Other")],
        "SOSStatusEnum": "apps.support.models.SOSStatus",
        "StaffRoleEnum": "apps.accounts.models.StaffRole",
    },
}

# --------------------------------------------------------------------------- business settings
CURRENCY = "NGN"
DEFAULT_CITY = os.environ.get("DEFAULT_CITY", "Lagos")
OTP_LENGTH = 6
OTP_TTL_SECONDS = 300
OTP_MAX_ATTEMPTS = 5
# DEV ONLY: when true, /auth/otp/request/ returns the code in the response so the
# Expo developer can log in without real SMS. Must be false in production.
OTP_DEBUG_RETURN_CODE = env_bool("OTP_DEBUG_RETURN_CODE", DEBUG)
SMS_BACKEND = os.environ.get("SMS_BACKEND", "console")          # console | twilio
PUSH_BACKEND = os.environ.get("PUSH_BACKEND", "console")        # console | expo (Expo push service) | fcm
PAYMENT_GATEWAY = os.environ.get("PAYMENT_GATEWAY", "dummy")    # dummy | paystack
DISPATCH_OFFER_SECONDS = int(os.environ.get("DISPATCH_OFFER_SECONDS", "15"))
DISPATCH_RADIUS_KM = float(os.environ.get("DISPATCH_RADIUS_KM", "5"))
DISPATCH_MAX_OFFERS = int(os.environ.get("DISPATCH_MAX_OFFERS", "3"))
PROVIDER_LOCATION_FRESH_SECONDS = 120
FARE_QUOTE_TTL_SECONDS = 600
SHARE_BASE_URL = os.environ.get("SHARE_BASE_URL", "https://chicanocruise.com/t/")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}

if not DEBUG:
    # Production hardening. Terminate TLS at the load balancer and forward the header.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
