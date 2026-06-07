from datetime import timedelta
from pathlib import Path

from decouple import config

from .env import get_bool_env, get_csv_env

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
    "apps.printing",
    "apps.announcements",
    "apps.notifications",
    "apps.chat",
    "apps.support",
    "apps.audit",
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
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
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

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Damascus"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = ROOT_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

MEDIA_URL = "/media/"
MEDIA_ROOT = ROOT_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CORS_ALLOWED_ORIGINS = get_csv_env(
    "CORS_ALLOWED_ORIGINS",
    "DJANGO_CORS_ALLOWED_ORIGINS",
    default=["http://localhost:3000", "http://localhost:5173"],
)
CORS_ALLOW_CREDENTIALS = True

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_FILTER_BACKENDS": ("django_filters.rest_framework.DjangoFilterBackend",),
    "DEFAULT_PAGINATION_CLASS": "apps.common.pagination.StandardPageNumberPagination",
    "PAGE_SIZE": 20,
    "EXCEPTION_HANDLER": "apps.common.exceptions.custom_exception_handler",
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.ScopedRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "auth_login": config("THROTTLE_AUTH_LOGIN", default="10/minute"),
        "otp_send": config("THROTTLE_OTP_SEND", default="5/minute"),
        "otp_verify": config("THROTTLE_OTP_VERIFY", default="10/minute"),
        "password_reset": config("THROTTLE_PASSWORD_RESET", default="5/minute"),
        "change_password": config("THROTTLE_CHANGE_PASSWORD", default="5/minute"),
        "chat_message": config("THROTTLE_CHAT_MESSAGE", default="30/minute"),
        "support_message": config("THROTTLE_SUPPORT_MESSAGE", default="20/minute"),
        "print_order": config("THROTTLE_PRINT_ORDER", default="10/minute"),
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(
        minutes=config("JWT_ACCESS_TOKEN_LIFETIME_MINUTES", default=60, cast=int)
    ),
    "REFRESH_TOKEN_LIFETIME": timedelta(
        days=config("JWT_REFRESH_TOKEN_LIFETIME_DAYS", default=14, cast=int)
    ),
    "ROTATE_REFRESH_TOKENS": False,
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
        "UserRoleEnum": [
            ("it_support", "IT Support"),
            ("admin", "Admin"),
            ("print_staff", "Print Staff"),
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
    },
}

REDIS_URL = config("REDIS_URL", default="redis://localhost:6379/0")
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
FCM_SERVER_KEY = config("FCM_SERVER_KEY", default="")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "panorama-local-cache",
    }
}

MAX_OTP_VERIFY_ATTEMPTS = config("MAX_OTP_VERIFY_ATTEMPTS", default=5, cast=int)
MAX_CHAT_MESSAGE_LENGTH = config("MAX_CHAT_MESSAGE_LENGTH", default=4000, cast=int)
MAX_IMAGE_UPLOAD_SIZE_MB = config("MAX_IMAGE_UPLOAD_SIZE_MB", default=5, cast=int)
MAX_DOCUMENT_UPLOAD_SIZE_MB = config("MAX_DOCUMENT_UPLOAD_SIZE_MB", default=25, cast=int)
ALLOWED_IMAGE_EXTENSIONS = get_csv_env(
    "ALLOWED_IMAGE_EXTENSIONS",
    default=["jpg", "jpeg", "png", "gif", "webp"],
)
ALLOWED_DOCUMENT_EXTENSIONS = get_csv_env(
    "ALLOWED_DOCUMENT_EXTENSIONS",
    default=["pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "txt", "jpg", "jpeg", "png"],
)

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [REDIS_URL]},
    }
}

RETURN_DEVELOPMENT_OTP = DEBUG
