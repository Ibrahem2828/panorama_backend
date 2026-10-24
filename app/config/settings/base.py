from datetime import timedelta
from pathlib import Path

from celery.schedules import crontab
from decouple import config

from .env import get_bool_env, get_csv_env
from .storage import build_storage_settings

BASE_DIR = Path(__file__).resolve().parent.parent.parent
ROOT_DIR = BASE_DIR.parent


def config_bool(name: str, default: bool = False) -> bool:
    return get_bool_env(name, default=default)


SECRET_KEY = config("SECRET_KEY", default="unsafe-development-secret-key")
DEBUG = get_bool_env("DEBUG", "DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = get_csv_env("ALLOWED_HOSTS", "DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

LOCAL_APPS = [
    "apps.common",
    "apps.accounts",
    "apps.universities",
    "apps.verification",
    "apps.enrollment",
    "apps.groups",
    "apps.files",
    "apps.lectures",
    "apps.printing",
    "apps.finance",
    "apps.announcements",
    "apps.notifications",
    "apps.chat",
    "apps.support",
    "apps.audit",
    "apps.feedback",
    "apps.product",
]

THIRD_PARTY_APPS = [
    "corsheaders",
    "django_filters",
    "drf_spectacular",
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "channels",
]

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.common.middleware.RequestIDMiddleware",
    "apps.common.middleware.APIErrorEnvelopeMiddleware",
    "apps.common.middleware.StructuredRequestLogMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.common.middleware.APISecurityHeadersMiddleware",
    "apps.product.middleware.ProductLifecycleMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config("DB_NAME", default="panorama_db"),
        "USER": config("DB_USER", default="panorama_user"),
        "PASSWORD": config("DB_PASSWORD", default="panorama_password"),
        "HOST": config("DB_HOST", default="localhost"),
        "PORT": config("DB_PORT", default="5432"),
    }
}

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ar"
TIME_ZONE = "Asia/Damascus"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = ROOT_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# Production mounts a named Coolify volume at /app/app/media. Static files
# remain separate under /app/staticfiles and continue to use WhiteNoise.
globals().update(
    build_storage_settings(
        base_dir=BASE_DIR,
        static_root=STATIC_ROOT,
        staticfiles_backend=STATICFILES_STORAGE,
    )
)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CORS_ALLOWED_ORIGINS = get_csv_env(
    "CORS_ALLOWED_ORIGINS",
    "DJANGO_CORS_ALLOWED_ORIGINS",
    default=["http://localhost:3000", "http://localhost:5173"],
)
CORS_ALLOW_CREDENTIALS = True

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ("apps.accounts.authentication.SessionVersionJWTAuthentication",),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_FILTER_BACKENDS": ("django_filters.rest_framework.DjangoFilterBackend",),
    "DEFAULT_PAGINATION_CLASS": "apps.common.pagination.StandardPageNumberPagination",
    "PAGE_SIZE": 20,
    "EXCEPTION_HANDLER": "apps.common.exceptions.custom_exception_handler",
    # Conservative global defaults; any view that declares throttle_classes overrides them.
    "DEFAULT_THROTTLE_CLASSES": (
        "apps.common.throttles.DefaultAnonRateThrottle",
        "apps.common.throttles.DefaultUserRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "user": config("THROTTLE_USER", default="600/min"),
        "anon": config("THROTTLE_ANON", default="120/min"),
        "auth_login": config("THROTTLE_AUTH_LOGIN", default="8/min"),
        "auth_register": config("THROTTLE_AUTH_REGISTER", default="4/hour"),
        "otp_request": config("THROTTLE_OTP_REQUEST", default="3/10min"),
        "otp_verify": config("THROTTLE_OTP_VERIFY", default="8/10min"),
        "password_reset": config("THROTTLE_PASSWORD_RESET", default="4/hour"),
        # Per-account budgets, independent of the source IP.
        "otp_request_account": config("THROTTLE_OTP_REQUEST_ACCOUNT", default="10/hour"),
        "otp_verify_account": config("THROTTLE_OTP_VERIFY_ACCOUNT", default="20/hour"),
        "password_reset_account": config("THROTTLE_PASSWORD_RESET_ACCOUNT", default="10/hour"),
        "feedback_submit": config("THROTTLE_FEEDBACK", default="20/day"),
        "file_ticket": config("THROTTLE_FILE_TICKET", default="60/hour"),
        "external_channel": config("THROTTLE_EXTERNAL_CHANNEL", default="20/hour"),
        "chat_message": config("THROTTLE_CHAT_MESSAGE", default="30/min"),
        "chat_ticket": config("THROTTLE_CHAT_TICKET", default="30/min"),
        "chat_report": config("THROTTLE_CHAT_REPORT", default="10/day"),
        "support_ticket": config("THROTTLE_SUPPORT_TICKET", default="5/hour"),
        "support_message": config("THROTTLE_SUPPORT_MESSAGE", default="30/hour"),
        "lecture_viewer": config("THROTTLE_LECTURE_VIEWER", default="240/min"),
        "lecture_notes": config("THROTTLE_LECTURE_NOTES", default="90/min"),
        "search": config("THROTTLE_SEARCH", default="60/min"),
        "mobile_device": config("THROTTLE_MOBILE_DEVICE", default="20/hour"),
        "web_push": config("THROTTLE_WEB_PUSH", default="30/hour"),
        "print_convert": config("THROTTLE_PRINT_CONVERT", default="20/hour"),
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=config("JWT_ACCESS_TOKEN_LIFETIME_MINUTES", default=15, cast=int)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=config("JWT_REFRESH_TOKEN_LIFETIME_DAYS", default=14, cast=int)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Panorama API",
    "DESCRIPTION": "Backend API for the Panorama student services platform.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SECURITY": [{"bearerAuth": []}],
    "ENUM_NAME_OVERRIDES": {
        "EnrollmentStatusEnum": "apps.enrollment.models.EnrollmentStatus",
        "EnrollmentItemStatusEnum": "apps.enrollment.models.EnrollmentItemStatus",
        "AccountDeletionStatusEnum": "apps.product.models.AccountDeletionStatus",
        "CategoryEnum": "apps.support.models.SupportTicketCategory",
        "FinanceCurrencyEnum": "apps.finance.models.FinanceCurrency",
        "InventoryCategoryEnum": "apps.finance.models.InventoryCategory",
        "ExpenseCategoryEnum": "apps.finance.models.ExpenseCategory",
        "StockMovementTypeEnum": "apps.finance.models.StockMovementType",
        "PaymentMethodEnum": "apps.finance.models.PaymentMethod",
        "UserRoleEnum": [
            ("it_support", "IT Support"),
            ("admin", "Admin"),
            ("print_staff", "Print Staff"),
            ("support_staff", "Support Staff"),
            ("content_manager", "Content Manager"),
            ("student", "Student"),
            ("normal_user", "Normal User"),
        ],
        "GroupMembershipRoleEnum": [
            ("member", "Member"),
            ("moderator", "Moderator"),
            ("group_admin", "Group Admin"),
        ],
        "MessageReportStatusEnum": [
            ("open", "Open"),
            ("reviewed", "Reviewed"),
            ("dismissed", "Dismissed"),
            ("message_deleted", "Message deleted"),
        ],
        "GroupSendMessagesPermissionEnum": [
            ("all_members", "All Members"),
            ("admins_only", "Admins Only"),
        ],
        "StudentVerificationStatusEnum": [
            ("incomplete", "Incomplete"),
            ("pending", "Pending"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
            ("needs_update", "Needs Update"),
            ("suspended", "Suspended"),
        ],
        "VerificationRequestStatusEnum": [
            ("pending", "Pending"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
            ("needs_update", "Needs Update"),
            ("cancelled", "Cancelled"),
        ],
        "GroupMembershipStatusEnum": [
            ("pending", "Pending"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
            ("blocked", "Blocked"),
            ("left", "Left"),
        ],
        "PrintOrderStatusEnum": [
            ("submitted", "Submitted"),
            ("under_review", "Under Review"),
            ("accepted", "Accepted"),
            ("printing", "Printing"),
            ("ready", "Ready"),
            ("delivered", "Delivered"),
            ("cancelled", "Cancelled"),
            ("rejected", "Rejected"),
        ],
        "PrintOrderPriorityEnum": [
            ("normal", "Normal"),
            ("student_priority", "Student Priority"),
            ("urgent", "Urgent"),
        ],
        "SupportTicketStatusEnum": [
            ("open", "Open"),
            ("in_progress", "In Progress"),
            ("waiting_user", "Waiting User"),
            ("resolved", "Resolved"),
            ("closed", "Closed"),
        ],
        "SupportTicketPriorityEnum": [
            ("low", "Low"),
            ("normal", "Normal"),
            ("high", "High"),
            ("urgent", "Urgent"),
        ],
        "FeedbackStatusEnum": [
            ("new", "New"),
            ("reviewing", "Reviewing"),
            ("planned", "Planned"),
            ("in_progress", "In Progress"),
            ("resolved", "Resolved"),
            ("rejected", "Rejected"),
            ("duplicate", "Duplicate"),
        ],
        "FeedbackPriorityEnum": [
            ("low", "Low"),
            ("normal", "Normal"),
            ("high", "High"),
            ("critical", "Critical"),
        ],
        "OtpChannelEnum": [
            ("email", "Email"),
            ("phone", "Phone"),
        ],
        "LectureProcessingStatusEnum": [
            ("uploaded", "Uploaded"),
            ("queued", "Queued"),
            ("scanning", "Scanning"),
            ("converting", "Converting"),
            ("extracting", "Extracting"),
            ("rendering", "Rendering"),
            ("ready", "Ready"),
            ("failed", "Failed"),
            ("quarantined", "Quarantined"),
        ],
        "MobilePlatformEnum": [
            ("android", "Android"),
            ("ios", "iOS"),
        ],
        "ConsentKindEnum": [
            ("terms", "Terms"),
            ("privacy", "Privacy"),
        ],
        "FeedbackKindEnum": [
            ("rating", "Rating"),
            ("suggestion", "Suggestion"),
            ("bug", "Bug Report"),
            ("complaint", "Complaint"),
            ("compliment", "Compliment"),
        ],
    },
}

REDIS_URL = config("REDIS_URL", default="redis://localhost:6379/0")
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
        "TIMEOUT": 300,
        "KEY_PREFIX": config("CACHE_KEY_PREFIX", default="panorama"),
        "VERSION": config("CACHE_KEY_VERSION", default=1, cast=int),
        "OPTIONS": {
            "socket_connect_timeout": config("REDIS_SOCKET_CONNECT_TIMEOUT", default=3, cast=int),
            "socket_timeout": config("REDIS_SOCKET_TIMEOUT", default=3, cast=int),
            "retry_on_timeout": True,
            "health_check_interval": config("REDIS_HEALTH_CHECK_INTERVAL", default=30, cast=int),
        },
    }
}

SUPPORT_EMAIL = config("SUPPORT_EMAIL", default="panoramacompany31@gmail.com")
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default=SUPPORT_EMAIL)
SERVER_EMAIL = config("SERVER_EMAIL", default=SUPPORT_EMAIL)
EMAIL_BACKEND = config("EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = config("EMAIL_HOST", default="smtp.gmail.com")
EMAIL_PORT = config("EMAIL_PORT", default=587, cast=int)
EMAIL_USE_TLS = get_bool_env("EMAIL_USE_TLS", default=True)
EMAIL_USE_SSL = get_bool_env("EMAIL_USE_SSL", default=False)
EMAIL_HOST_USER = config("EMAIL_HOST_USER", default=SUPPORT_EMAIL)
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="")
EMAIL_TIMEOUT = config("EMAIL_TIMEOUT", default=15, cast=int)

# Per-account login protection: this many consecutive failures for one identifier (any source IP) inside the window
# lock that identifier for LOGIN_LOCKOUT_MINUTES. Applies to unknown identifiers too, so it never reveals accounts.
LOGIN_LOCKOUT_THRESHOLD = config("LOGIN_LOCKOUT_THRESHOLD", default=10, cast=int)
LOGIN_LOCKOUT_WINDOW_MINUTES = config("LOGIN_LOCKOUT_WINDOW_MINUTES", default=15, cast=int)
LOGIN_LOCKOUT_MINUTES = config("LOGIN_LOCKOUT_MINUTES", default=15, cast=int)

OTP_DEFAULT_CHANNEL = config("OTP_DEFAULT_CHANNEL", default="email")
OTP_EXPIRY_MINUTES = config("OTP_EXPIRY_MINUTES", default=10, cast=int)
OTP_RESEND_COOLDOWN_SECONDS = config("OTP_RESEND_COOLDOWN_SECONDS", default=60, cast=int)
OTP_MAX_ATTEMPTS = config("OTP_MAX_ATTEMPTS", default=5, cast=int)
OTP_EMAIL_SUBJECT = config("OTP_EMAIL_SUBJECT", default="رمز التحقق الخاص بتطبيق بانوراما")
# Send OTP emails from a Celery worker instead of inside the request (production enables it).
OTP_EMAIL_ASYNC = get_bool_env("OTP_EMAIL_ASYNC", default=False)
SMS_OTP_PROVIDER_ENABLED = get_bool_env("SMS_OTP_PROVIDER_ENABLED", default=False)

FIELD_ENCRYPTION_KEY = config("FIELD_ENCRYPTION_KEY", default="")
APP_BASE_URL = config("APP_BASE_URL", default="http://localhost:8000").rstrip("/")
FILE_ACCESS_TICKET_TTL_SECONDS = config("FILE_ACCESS_TICKET_TTL_SECONDS", default=120, cast=int)
FILE_ACCESS_TICKET_MAX_USES = config("FILE_ACCESS_TICKET_MAX_USES", default=8, cast=int)
EXTERNAL_CHANNEL_TICKET_TTL_SECONDS = config("EXTERNAL_CHANNEL_TICKET_TTL_SECONDS", default=60, cast=int)
WEBSOCKET_CHAT_TICKET_TTL_SECONDS = config("WEBSOCKET_CHAT_TICKET_TTL_SECONDS", default=45, cast=int)
WEBSOCKET_LEGACY_JWT_QUERY_AUTH_ENABLED = get_bool_env("WEBSOCKET_LEGACY_JWT_QUERY_AUTH_ENABLED", default=False)
VERIFICATION_CARD_RETENTION_DAYS = config("VERIFICATION_CARD_RETENTION_DAYS", default=90, cast=int)
OTP_RETENTION_DAYS = config("OTP_RETENTION_DAYS", default=7, cast=int)
ACCESS_TICKET_RETENTION_HOURS = config("ACCESS_TICKET_RETENTION_HOURS", default=24, cast=int)
FEEDBACK_PROMPT_EVENT_RETENTION_DAYS = config("FEEDBACK_PROMPT_EVENT_RETENTION_DAYS", default=365, cast=int)
FEEDBACK_ABUSE_TERMS = get_csv_env("FEEDBACK_ABUSE_TERMS", default=[])
FEEDBACK_AI_TRIAGE_ENABLED = get_bool_env("FEEDBACK_AI_TRIAGE_ENABLED", default=False)
FEEDBACK_AI_PROVIDER = config("FEEDBACK_AI_PROVIDER", default="local_safe_heuristic")
FEEDBACK_AI_CIRCUIT_SECONDS = config("FEEDBACK_AI_CIRCUIT_SECONDS", default=300, cast=int)
AUDIT_LOG_RETENTION_DAYS = config("AUDIT_LOG_RETENTION_DAYS", default=730, cast=int)
MAX_DOCUMENT_UPLOAD_SIZE = config("MAX_DOCUMENT_UPLOAD_SIZE", default=25 * 1024 * 1024, cast=int)
MAX_IMAGE_UPLOAD_SIZE = config("MAX_IMAGE_UPLOAD_SIZE", default=8 * 1024 * 1024, cast=int)
LECTURE_MAX_UPLOAD_SIZE = config("LECTURE_MAX_UPLOAD_SIZE", default=50 * 1024 * 1024, cast=int)
LECTURE_MAX_PAGES = config("LECTURE_MAX_PAGES", default=500, cast=int)
PRINT_CONVERSION_MAX_PAGES = config("PRINT_CONVERSION_MAX_PAGES", default=300, cast=int)
PRINT_CONVERSION_TTL_SECONDS = config("PRINT_CONVERSION_TTL_SECONDS", default=7200, cast=int)
PRINT_CONVERSION_WAIT_SECONDS = config("PRINT_CONVERSION_WAIT_SECONDS", default=60, cast=int)
LECTURE_VIEWER_SESSION_TTL_SECONDS = config("LECTURE_VIEWER_SESSION_TTL_SECONDS", default=900, cast=int)
LECTURE_VIEWER_SESSION_MAX_PAGE_REQUESTS = config("LECTURE_VIEWER_SESSION_MAX_PAGE_REQUESTS", default=1200, cast=int)
DOCUMENT_CONVERSION_TIME_LIMIT = config("DOCUMENT_CONVERSION_TIME_LIMIT", default=180, cast=int)
DOCUMENT_CONVERSION_SOFT_TIME_LIMIT = config("DOCUMENT_CONVERSION_SOFT_TIME_LIMIT", default=150, cast=int)
DATA_UPLOAD_MAX_MEMORY_SIZE = config("DATA_UPLOAD_MAX_MEMORY_SIZE", default=30 * 1024 * 1024, cast=int)
FILE_UPLOAD_MAX_MEMORY_SIZE = config("FILE_UPLOAD_MAX_MEMORY_SIZE", default=5 * 1024 * 1024, cast=int)
API_CONTENT_SECURITY_POLICY = config(
    "API_CONTENT_SECURITY_POLICY", default="default-src 'none'; frame-ancestors 'none'"
)

CELERY_BROKER_URL = config("CELERY_BROKER_URL", default=REDIS_URL)
CELERY_RESULT_BACKEND = config("CELERY_RESULT_BACKEND", default=REDIS_URL)
CELERY_TASK_TIME_LIMIT = config("CELERY_TASK_TIME_LIMIT", default=60, cast=int)
CELERY_TASK_SOFT_TIME_LIMIT = config("CELERY_TASK_SOFT_TIME_LIMIT", default=45, cast=int)
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_RESULT_EXPIRES = config("CELERY_RESULT_EXPIRES_SECONDS", default=3600, cast=int)
CELERY_TASK_ROUTES = {
    "apps.lectures.tasks.*": {"queue": "conversion"},
    "apps.printing.tasks.convert_print_document": {"queue": "conversion"},
}
# Redelivery window for unacknowledged tasks; must exceed the longest task (lecture conversion).
CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": 7200}
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_MAX_TASKS_PER_CHILD = 200  # recycle workers; LibreOffice and Pillow leak over time
CELERY_BEAT_SCHEDULE = {
    "execute-due-account-deletions": {
        "task": "apps.product.tasks.execute_due_account_deletions",
        "schedule": 3600.0,
    },
    # Retention: OTPs, access tickets, expired overrides, verification-card images, audit rows.
    "purge-expired-sensitive-data": {
        "task": "apps.common.tasks.purge_expired_sensitive_data",
        "schedule": crontab(hour=3, minute=15),
    },
    # Expired idempotency rows are no longer deleted inside every keyed request.
    "purge-expired-idempotency-records": {
        "task": "apps.product.tasks.purge_expired_idempotency_records",
        "schedule": crontab(hour=3, minute=45),
    },
    # Audit rows older than AUDIT_LOG_RETENTION_DAYS (<= 0 keeps them forever), deleted in batches of 1000.
    "purge-expired-audit-logs": {
        "task": "apps.audit.tasks.purge_expired_audit_logs",
        "schedule": crontab(hour=3, minute=55),
    },
    # SimpleJWT token_blacklist tables otherwise grow without bound.
    "flush-expired-jwt-tokens": {
        "task": "apps.common.tasks.flush_expired_jwt_tokens",
        "schedule": crontab(hour=4, minute=5),
    },
    "purge-expired-print-conversions": {
        "task": "apps.printing.tasks.purge_expired_print_conversions",
        "schedule": 1800.0,
    },
    # Lectures whose worker died mid-conversion would otherwise stay "processing" forever.
    "fail-stuck-lectures": {
        "task": "apps.lectures.tasks.fail_stuck_lectures",
        "schedule": 600.0,
    },
}
FCM_SERVER_KEY = config("FCM_SERVER_KEY", default="")

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [REDIS_URL]},
    }
}

RETURN_DEVELOPMENT_OTP = DEBUG and get_bool_env("RETURN_DEVELOPMENT_OTP", default=False)
API_DOCS_ENABLED = get_bool_env("API_DOCS_ENABLED", default=DEBUG)
# Django admin site: mounted only when explicitly enabled (dev/test/e2e default to DEBUG=True; production to False).
ADMIN_ENABLED = get_bool_env("ADMIN_ENABLED", default=DEBUG)
TRUSTED_PROXY_COUNT = config("TRUSTED_PROXY_COUNT", default=1, cast=int)
# Optional shared secret that lets our Next.js BFF (web + dashboard) assert the real end-user IP through
# X-Panorama-Client-IP. Empty disables the feature. Never log or expose it; never add the headers to CORS.
BFF_SHARED_SECRET = config("BFF_SHARED_SECRET", default="")
ACCOUNT_DELETION_GRACE_DAYS = config("ACCOUNT_DELETION_GRACE_DAYS", default=30, cast=int)


PUSH_NOTIFICATIONS_ENABLED = get_bool_env("PUSH_NOTIFICATIONS_ENABLED", default=False)
EXPO_PUSH_ENDPOINT = config("EXPO_PUSH_ENDPOINT", default="https://exp.host/--/api/v2/push/send")
EXPO_ACCESS_TOKEN = config("EXPO_ACCESS_TOKEN", default="")
EXPO_PUSH_ALLOWED_HOSTS = frozenset(
    host.strip().lower()
    for host in config("EXPO_PUSH_ALLOWED_HOSTS", default="exp.host,api.expo.dev").split(",")
    if host.strip()
)

# Browser Web Push (VAPID) for the student PWA. The feature is configured only when all three values are set.
# Generate a pair with docs/WEB_PUSH.md; never commit or log the private key.
WEB_PUSH_VAPID_PUBLIC_KEY = config("WEB_PUSH_VAPID_PUBLIC_KEY", default="").strip()
WEB_PUSH_VAPID_PRIVATE_KEY = config("WEB_PUSH_VAPID_PRIVATE_KEY", default="").strip()
WEB_PUSH_VAPID_SUBJECT = config("WEB_PUSH_VAPID_SUBJECT", default="").strip()  # mailto: or https: contact URI
WEB_PUSH_ENABLED = bool(WEB_PUSH_VAPID_PUBLIC_KEY and WEB_PUSH_VAPID_PRIVATE_KEY and WEB_PUSH_VAPID_SUBJECT)
# Push-service host suffixes a subscription endpoint may point at (exact host or any subdomain). Add a new browser
# vendor here instead of changing code.
WEB_PUSH_ALLOWED_HOST_SUFFIXES = tuple(
    host.strip().lower().lstrip(".")
    for host in config(
        "WEB_PUSH_ALLOWED_HOST_SUFFIXES",
        default="fcm.googleapis.com,updates.push.services.mozilla.com,push.apple.com,notify.windows.com",
    ).split(",")
    if host.strip()
)
WEB_PUSH_TIMEOUT_SECONDS = config("WEB_PUSH_TIMEOUT_SECONDS", default=5, cast=int)
# Consecutive non-retryable 4xx answers (other than 404/410) after which a subscription is deactivated.
WEB_PUSH_MAX_CONSECUTIVE_FAILURES = config("WEB_PUSH_MAX_CONSECUTIVE_FAILURES", default=5, cast=int)
