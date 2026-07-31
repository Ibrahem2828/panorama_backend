from django.apps import AppConfig


class ProductConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.product"
    verbose_name = "Product controls and mobile integration"

    def ready(self) -> None:
        from . import signals  # noqa: F401
