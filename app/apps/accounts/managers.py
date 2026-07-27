from django.contrib.auth.base_user import BaseUserManager

from .choices import UserRole


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _normalize_email(self, email: str) -> str:
        return self.normalize_email(email).lower()

    def create_user(self, email: str, phone_number: str, password: str | None = None, **extra_fields):
        if not email:
            raise ValueError("Users must have an email address")
        if not phone_number:
            raise ValueError("Users must have a phone number")

        email = self._normalize_email(email)
        user = self.model(email=email, phone_number=phone_number.strip(), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email: str, phone_number: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("role", UserRole.IT_SUPPORT)
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True")

        return self.create_user(email=email, phone_number=phone_number, password=password, **extra_fields)
