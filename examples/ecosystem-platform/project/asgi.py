"""Route HTTP/lifespan to Django and WebSockets to Channels."""

import os

from aiodrf_asgi_lifespan.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "project.settings")
django_application = get_asgi_application()

from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402
from demo.consumers import Records  # noqa: E402
from django.urls import path  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_application,
        "lifespan": django_application,
        "websocket": AllowedHostsOriginValidator(
            AuthMiddlewareStack(URLRouter([path("ws/records/", Records.as_asgi())]))
        ),
    }
)
