"""
ASGI config for COORDINATE.
Handles both HTTP (via Django) and WebSocket (via Django Channels).
Supports web sessions and mobile Bearer tokens via TokenOrSessionAuthMiddlewareStack.
"""

import os

from django.conf import settings
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'campus.settings.prod')

# Initialise Django before importing anything that touches the ORM.
django_asgi_app = get_asgi_application()

from coordinate.routing import websocket_urlpatterns  # noqa: E402
from campus.api.channels_middleware import (  # noqa: E402
    MobileAndAllowedHostsOriginValidator,
    TokenOrSessionAuthMiddlewareStack,
)
from accounts.client_logger import ClientLoggingASGIWrapper  # noqa: E402

application = ClientLoggingASGIWrapper(
    ProtocolTypeRouter({
        'http': django_asgi_app,
        'websocket': MobileAndAllowedHostsOriginValidator(
            TokenOrSessionAuthMiddlewareStack(
                URLRouter(websocket_urlpatterns)
            ),
            allowed_origins=settings.ALLOWED_HOSTS,
        ),
    })
)
