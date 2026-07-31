import os

from config.settings.env import get_bool_env
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from apps.accounts.choices import UserRole
from apps.accounts.models import User

LOCAL_DEFAULTS = {
    "django_superuser": {
        "email": "it@panorama.local",
        "phone": "+963900000001",
        "full_name": "Panorama IT Support",
    },
    "dashboard_admin": {
        "email": "admin@panorama.local",
        "phone": "+963900000002",
        "full_name": "Panorama Admin",
    },
    "print_staff": {
        "email": "print@panorama.local",
        "phone": "+963900000003",
        "full_name": "Panorama Print Staff",
    },
}


ACCOUNT_SPECS = [
    {
        "key": "django_superuser",
        "label": "Django superuser / IT Support",
        "prefix": "DJANGO_SUPERUSER",
        "role": UserRole.IT_SUPPORT,
        "is_staff": True,
        "is_superuser": True,
    },
    {
        "key": "dashboard_admin",
        "label": "Dashboard Admin",
        "prefix": "DASHBOARD_ADMIN",
        "role": UserRole.ADMIN,
        "is_staff": True,
        "is_superuser": False,
    },
    {
        "key": "print_staff",
        "label": "Print Staff",
        "prefix": "PRINT_STAFF",
        "role": UserRole.PRINT_STAFF,
        "is_staff": False,
        "is_superuser": False,
    },
]


class Command(BaseCommand):
    help = "Create or update required Panorama production admin accounts."

    def handle(self, *args, **options):
        reset_passwords = get_bool_env("RESET_ADMIN_PASSWORDS", default=False)
        for spec in ACCOUNT_SPECS:
            account = self._account_from_env(spec)
            user, created = self._find_or_create_user(account)
            self._apply_required_fields(user, account, spec, created, reset_passwords)
            status = "created" if created else "updated"
            self.stdout.write(self.style.SUCCESS(f"{spec['label']}: {status} ({user.email})"))

    def _account_from_env(self, spec: dict) -> dict:
        prefix = spec["prefix"]
        defaults = LOCAL_DEFAULTS[spec["key"]] if settings.DEBUG else {}
        account = {
            "email": self._env_or_default(f"{prefix}_EMAIL", defaults.get("email")),
            "phone": self._env_or_default(f"{prefix}_PHONE", defaults.get("phone")),
            "password": self._env_or_default(f"{prefix}_PASSWORD"),
            "full_name": self._env_or_default(f"{prefix}_FULL_NAME", defaults.get("full_name")),
        }
        missing = [key for key, value in account.items() if not value]
        if missing:
            env_suffixes = {"email": "EMAIL", "phone": "PHONE", "password": "PASSWORD", "full_name": "FULL_NAME"}
            raise CommandError(
                f"{spec['label']} is not configured. Missing env vars: "
                f"{', '.join(f'{prefix}_{env_suffixes[key]}' for key in missing)}"
            )
        return account

    def _find_or_create_user(self, account: dict) -> tuple[User, bool]:
        matches = list(User.objects.filter(Q(email__iexact=account["email"]) | Q(phone_number=account["phone"]))[:2])
        if len(matches) > 1:
            raise CommandError(f"Cannot configure account {account['email']}: email and phone match different users.")
        if matches:
            return matches[0], False
        return (
            User.objects.create_user(
                email=account["email"],
                phone_number=account["phone"],
                password=account["password"],
                full_name=account["full_name"],
            ),
            True,
        )

    def _apply_required_fields(
        self, user: User, account: dict, spec: dict, created: bool, reset_passwords: bool
    ) -> None:
        user.email = account["email"].lower()
        user.phone_number = account["phone"]
        user.full_name = account["full_name"]
        user.role = spec["role"]
        user.is_active = True
        user.is_staff = spec["is_staff"]
        user.is_superuser = spec["is_superuser"]
        if hasattr(user, "is_phone_verified"):
            user.is_phone_verified = True
        if created or reset_passwords:
            user.set_password(account["password"])
        user.save()

    def _env_or_default(self, name: str, default: str | None = None) -> str | None:
        value = os.environ.get(name)
        return value.strip() if value and value.strip() else default
