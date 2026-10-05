import os

from channels.routing import (
    ProtocolTypeRouter,
    URLRouter,
)
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

django_asgi_app = get_asgi_application()


class OptionalOriginValidator:
    """Reject cross-site browser handshakes without locking out native clients.

    Browsers always send ``Origin`` and it must match ``ALLOWED_HOSTS``. Native clients (the React Native mobile app)
    send no ``Origin`` at all, and ``AllowedHostsOriginValidator`` rejects that, so such handshakes go straight to the
    router where the single-use chat ticket authenticates them.
    """

    def __init__(self, app):
        self.app = app
        self.browser_app = AllowedHostsOriginValidator(app)

    async def __call__(self, scope, receive, send):
        has_origin = any(name.lower() == b"origin" for name, _ in scope.get("headers", []))
        return await (self.browser_app if has_origin else self.app)(scope, receive, send)


from apps.chat.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": OptionalOriginValidator(URLRouter(websocket_urlpatterns)),
    }
)
