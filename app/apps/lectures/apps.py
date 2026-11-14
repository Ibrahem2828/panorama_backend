from django.apps import AppConfig


class LecturesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.lectures"

    def ready(self) -> None:
        from . import signals  # noqa: F401
