import importlib
import sys

import pytest
from decouple import UndefinedValueError
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.choices import UserRole
from apps.accounts.models import User
from config.settings.env import get_bool_env, get_csv_env


def test_get_csv_env_uses_primary_before_fallback(monkeypatch):
    monkeypatch.setenv("ALLOWED_HOSTS", "api.example.com,localhost")
    monkeypatch.setenv("DJANGO_ALLOWED_HOSTS", "fallback.example.com")

    assert get_csv_env("ALLOWED_HOSTS", "DJANGO_ALLOWED_HOSTS") == ["api.example.com", "localhost"]


def test_get_csv_env_uses_fallback(monkeypatch):
    monkeypatch.delenv("ALLOWED_HOSTS", raising=False)
    monkeypatch.setenv("DJANGO_ALLOWED_HOSTS", "api.example.com,127.0.0.1")
    monkeypatch.setattr("config.settings.env.config", lambda name: (_ for _ in ()).throw(UndefinedValueError(name)))

    assert get_csv_env("ALLOWED_HOSTS", "DJANGO_ALLOWED_HOSTS") == ["api.example.com", "127.0.0.1"]


def test_get_csv_env_missing_required_has_readable_error(monkeypatch):
    monkeypatch.delenv("ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("DJANGO_ALLOWED_HOSTS", raising=False)
    monkeypatch.setattr("config.settings.env.config", lambda name: (_ for _ in ()).throw(UndefinedValueError(name)))

    with pytest.raises(ImproperlyConfigured, match="Production requires ALLOWED_HOSTS"):
        get_csv_env(
            "ALLOWED_HOSTS",
            "DJANGO_ALLOWED_HOSTS",
            required_message="Production requires ALLOWED_HOSTS or DJANGO_ALLOWED_HOSTS.",
        )


def test_get_bool_env_supports_django_debug_fallback(monkeypatch):
    monkeypatch.delenv("DEBUG", raising=False)
    monkeypatch.setenv("DJANGO_DEBUG", "True")
    monkeypatch.setattr("config.settings.env.config", lambda name: (_ for _ in ()).throw(UndefinedValueError(name)))

    assert get_bool_env("DEBUG", "DJANGO_DEBUG", default=False) is True


def test_production_settings_accept_django_allowed_hosts(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", "test-encryption-key")
    monkeypatch.setenv("EMAIL_HOST_PASSWORD", "test-smtp-password")
    monkeypatch.delenv("ALLOWED_HOSTS", raising=False)
    monkeypatch.setenv("DJANGO_ALLOWED_HOSTS", "coolify.sslip.io,localhost")
    monkeypatch.setenv("DATABASE_URL", "postgres://user:pass@localhost:5432/panorama")
    monkeypatch.setenv("DATABASE_SSL_REQUIRE", "True")
    monkeypatch.setattr("config.settings.env.config", lambda name: (_ for _ in ()).throw(UndefinedValueError(name)))

    sys.modules.pop("config.settings.production", None)
    production = importlib.import_module("config.settings.production")

    assert production.ALLOWED_HOSTS == ["coolify.sslip.io", "localhost"]
    assert production.DEBUG is False
    assert production.DATABASES["default"]["OPTIONS"] == {"sslmode": "require"}


def test_health_endpoint_is_public():
    response = APIClient().get("/api/v1/health/")

    assert response.status_code == status.HTTP_200_OK
    assert response.data == {
        "code": "OK",
        "success": True,
        "message": "OK",
        "data": {
            "status": "healthy",
            "service": "panorama_backend",
        },
    }


@pytest.mark.django_db
def test_setup_admin_accounts_creates_required_users_idempotently(monkeypatch, capsys):
    monkeypatch.setenv("DJANGO_SUPERUSER_EMAIL", "it-prod@example.com")
    monkeypatch.setenv("DJANGO_SUPERUSER_PHONE", "+963900100001")
    monkeypatch.setenv("DJANGO_SUPERUSER_PASSWORD", "StrongPass123!")
    monkeypatch.setenv("DJANGO_SUPERUSER_FULL_NAME", "Production IT")
    monkeypatch.setenv("DASHBOARD_ADMIN_EMAIL", "admin-prod@example.com")
    monkeypatch.setenv("DASHBOARD_ADMIN_PHONE", "+963900100002")
    monkeypatch.setenv("DASHBOARD_ADMIN_PASSWORD", "StrongPass123!")
    monkeypatch.setenv("DASHBOARD_ADMIN_FULL_NAME", "Production Admin")
    monkeypatch.setenv("PRINT_STAFF_EMAIL", "print-prod@example.com")
    monkeypatch.setenv("PRINT_STAFF_PHONE", "+963900100003")
    monkeypatch.setenv("PRINT_STAFF_PASSWORD", "StrongPass123!")
    monkeypatch.setenv("PRINT_STAFF_FULL_NAME", "Production Print")
    monkeypatch.setenv("RESET_ADMIN_PASSWORDS", "False")

    call_command("setup_admin_accounts")
    call_command("setup_admin_accounts")
    output = capsys.readouterr().out

    assert "StrongPass123!" not in output
    assert User.objects.filter(email="it-prod@example.com").count() == 1
    assert User.objects.filter(email="admin-prod@example.com").count() == 1
    assert User.objects.filter(email="print-prod@example.com").count() == 1

    it_user = User.objects.get(email="it-prod@example.com")
    assert it_user.role == UserRole.IT_SUPPORT
    assert it_user.is_staff is True
    assert it_user.is_superuser is True
    assert it_user.is_phone_verified is True

    dashboard_admin = User.objects.get(email="admin-prod@example.com")
    assert dashboard_admin.role == UserRole.ADMIN
    assert dashboard_admin.is_staff is True
    assert dashboard_admin.is_superuser is False

    print_staff = User.objects.get(email="print-prod@example.com")
    assert print_staff.role == UserRole.PRINT_STAFF
    assert print_staff.is_superuser is False
