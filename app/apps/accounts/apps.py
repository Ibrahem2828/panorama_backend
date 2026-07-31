from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"

    def ready(self):
        # Importing extensions at app startup registers OpenAPI integrations
        # without coupling request authentication to schema generation.
        from . import schema  # noqa: F401
