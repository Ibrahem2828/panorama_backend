from django.apps import AppConfig


class FilesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.files"

    def ready(self) -> None:
        from . import signals  # noqa: F401
