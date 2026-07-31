"""drf-spectacular extensions for Panorama account authentication."""

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class SessionVersionJWTAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "apps.accounts.authentication.SessionVersionJWTAuthentication"
    name = "jwtAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }
