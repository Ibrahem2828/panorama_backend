import importlib
import sys

from decouple import UndefinedValueError
from django.conf import settings
from rest_framework import status
from rest_framework.test import APIClient


def _read_repo_file(relative_path):
    return (settings.ROOT_DIR / relative_path).read_text(encoding="utf-8")


def _import_production_settings(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "phase-5-production-secret")
    monkeypatch.setenv("DEBUG", "False")
    monkeypatch.setenv("ALLOWED_HOSTS", "api.example.com")
    monkeypatch.setenv("CSRF_TRUSTED_ORIGINS", "https://api.example.com")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://dashboard.example.com")
    monkeypatch.setenv("DATABASE_URL", "postgres://user:pass@localhost:5432/panorama")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setattr("config.settings.env.config", lambda name: (_ for _ in ()).throw(UndefinedValueError(name)))
    sys.modules.pop("config.settings.production", None)
    return importlib.import_module("config.settings.production")


def test_health_and_readiness_endpoints_remain_public(db):
    client = APIClient()

    health = client.get("/api/v1/health/")
    ready = client.get("/api/v1/health/ready/")

    assert health.status_code == status.HTTP_200_OK
    assert ready.status_code == status.HTTP_200_OK
    assert health["X-Request-ID"]
    assert ready.data["data"]["database"] == "healthy"
    assert ready.data["data"]["cache"] == "healthy"


def test_production_settings_import_and_map_runtime_services(monkeypatch):
    production = _import_production_settings(monkeypatch)

    assert production.STATIC_ROOT.name == "staticfiles"
    assert production.MEDIA_ROOT.name == "media"
    assert production.CACHES["default"]["LOCATION"] == "redis://localhost:6379/0"
    assert production.CHANNEL_LAYERS["default"]["CONFIG"]["hosts"] == ["redis://localhost:6379/0"]
    assert production.CELERY_BROKER_URL == "redis://localhost:6379/0"
    assert production.CELERY_RESULT_BACKEND == "redis://localhost:6379/0"
    assert production.CELERY_BEAT_SCHEDULE["cleanup-expired-otp-daily"]["task"] == "apps.common.tasks.cleanup_expired_otp"


def test_celery_app_is_configured():
    import apps.common.tasks  # noqa: F401
    from config.celery import app

    assert app.main == "panorama"
    assert "apps.common.tasks.cleanup_expired_otp" in app.tasks


def test_dockerfile_is_production_oriented():
    dockerfile = _read_repo_file("Dockerfile")

    assert "FROM python:3.12-slim" in dockerfile
    assert "requirements/production.txt" in dockerfile
    assert "USER panorama" in dockerfile
    assert "daphne -b 0.0.0.0" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "runserver" not in dockerfile


def test_dockerignore_excludes_sensitive_and_runtime_artifacts():
    dockerignore = _read_repo_file(".dockerignore")

    for pattern in (".env", ".env.*", "media/", "staticfiles/", "logs/", "__pycache__/", ".pytest_cache/", "*.sqlite3"):
        assert pattern in dockerignore
    assert "!.env.example" in dockerignore


def test_coolify_compose_uses_safe_services_and_placeholders():
    compose = _read_repo_file("docker-compose.coolify.yml")

    assert "services:" in compose
    assert "web:" in compose
    assert "postgres:" in compose
    assert "redis:" in compose
    assert "worker:" in compose
    assert "beat:" in compose
    assert "${SECRET_KEY:?set SECRET_KEY}" in compose
    assert "${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD}" in compose
    assert "ports:" not in compose
    assert "condition: service_healthy" in compose


def test_production_docs_exist():
    for relative_path in (
        "docs/COOLIFY_DEPLOYMENT.md",
        "docs/ENVIRONMENT_PRODUCTION.md",
        "docs/BACKUP_RESTORE.md",
        "docs/PRODUCTION_SECURITY_CHECKLIST.md",
        "docs/PRODUCTION_SMOKE_TESTS.md",
        "docs/34_PHASE_5_COOLIFY_PRODUCTION_DEPLOYMENT_READINESS.md",
    ):
        assert (settings.ROOT_DIR / relative_path).exists()
