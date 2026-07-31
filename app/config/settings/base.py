from datetime import timedelta
from pathlib import Path

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
    "apps.groups",
    "apps.files",
    "apps.lectures",
    "apps.printing",
    "apps.announcements",
    "apps.notifications",
    "apps.chat",
    "apps.support",
    "apps.audit",
    "apps.feedback",
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
    "DEFAULT_AUTHENTICATION_CLASSES": ("rest_framework_simplejwt.authentication.JWTAuthentication",),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_FILTER_BACKENDS": ("django_filters.rest_framework.DjangoFilterBackend",),
    "DEFAULT_PAGINATION_CLASS": "apps.common.pagination.StandardPageNumberPagination",
    "PAGE_SIZE": 20,
    "EXCEPTION_HANDLER": "apps.common.exceptions.custom_exception_handler",
    "DEFAULT_THROTTLE_RATES": {
        "auth_login": config("THROTTLE_AUTH_LOGIN", default="8/min"),
        "auth_register": config("THROTTLE_AUTH_REGISTER", default="4/hour"),
        "otp_request": config("THROTTLE_OTP_REQUEST", default="3/10min"),
        "otp_verify": config("THROTTLE_OTP_VERIFY", default="8/10min"),
        "password_reset": config("THROTTLE_PASSWORD_RESET", default="4/hour"),
        "feedback_submit": config("THROTTLE_FEEDBACK", default="20/day"),
        "file_ticket": config("THROTTLE_FILE_TICKET", default="60/hour"),
        "external_channel": config("THROTTLE_EXTERNAL_CHANNEL", default="20/hour"),
        "chat_message": config("THROTTLE_CHAT_MESSAGE", default="30/min"),
        "chat_report": config("THROTTLE_CHAT_REPORT", default="10/day"),
        "support_ticket": config("THROTTLE_SUPPORT_TICKET", default="5/hour"),
        "support_message": config("THROTTLE_SUPPORT_MESSAGE", default="30/hour"),
        "lecture_viewer": config("THROTTLE_LECTURE_VIEWER", default="240/min"),
        "lecture_notes": config("THROTTLE_LECTURE_NOTES", default="90/min"),
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=config("JWT_ACCESS_TOKEN_LIFETIME_MINUTES", default=60, cast=int)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=config("JWT_REFRESH_TOKEN_LIFETIME_DAYS", default=14, cast=int)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Panorama API",
    "DESCRIPTION": "Backend API for the Panorama student services platform.",
    "VERSION": "2.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SECURITY": [{"bearerAuth": []}],
    "ENUM_NAME_OVERRIDES": {
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
        "MessageReportStatusEnum": [
            ("open", "Open"),
            ("reviewed", "Reviewed"),
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

OTP_DEFAULT_CHANNEL = config("OTP_DEFAULT_CHANNEL", default="email")
OTP_EXPIRY_MINUTES = config("OTP_EXPIRY_MINUTES", default=10, cast=int)
OTP_RESEND_COOLDOWN_SECONDS = config("OTP_RESEND_COOLDOWN_SECONDS", default=60, cast=int)
OTP_MAX_ATTEMPTS = config("OTP_MAX_ATTEMPTS", default=5, cast=int)
OTP_EMAIL_SUBJECT = config("OTP_EMAIL_SUBJECT", default="رمز التحقق الخاص بتطبيق بانوراما")
SMS_OTP_PROVIDER_ENABLED = get_bool_env("SMS_OTP_PROVIDER_ENABLED", default=False)

FIELD_ENCRYPTION_KEY = config("FIELD_ENCRYPTION_KEY", default="")
APP_BASE_URL = config("APP_BASE_URL", default="http://localhost:8000").rstrip("/")
FILE_ACCESS_TICKET_TTL_SECONDS = config("FILE_ACCESS_TICKET_TTL_SECONDS", default=120, cast=int)
FILE_ACCESS_TICKET_MAX_USES = config("FILE_ACCESS_TICKET_MAX_USES", default=8, cast=int)
EXTERNAL_CHANNEL_TICKET_TTL_SECONDS = config("EXTERNAL_CHANNEL_TICKET_TTL_SECONDS", default=60, cast=int)
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
CELERY_TASK_ROUTES = {"apps.lectures.tasks.*": {"queue": "conversion"}}
FCM_SERVER_KEY = config("FCM_SERVER_KEY", default="")

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [REDIS_URL]},
    }
}

RETURN_DEVELOPMENT_OTP = DEBUG and get_bool_env("RETURN_DEVELOPMENT_OTP", default=False)
API_DOCS_ENABLED = get_bool_env("API_DOCS_ENABLED", default=DEBUG)
TRUSTED_PROXY_COUNT = config("TRUSTED_PROXY_COUNT", default=1, cast=int)


PUSH_NOTIFICATIONS_ENABLED = get_bool_env("PUSH_NOTIFICATIONS_ENABLED", default=False)
EXPO_PUSH_ENDPOINT = config("EXPO_PUSH_ENDPOINT", default="https://exp.host/--/api/v2/push/send")
EXPO_ACCESS_TOKEN = config("EXPO_ACCESS_TOKEN", default="")
EXPO_PUSH_ALLOWED_HOSTS = frozenset(
    host.strip().lower()
    for host in config("EXPO_PUSH_ALLOWED_HOSTS", default="exp.host,api.expo.dev").split(",")
    if host.strip()
)
