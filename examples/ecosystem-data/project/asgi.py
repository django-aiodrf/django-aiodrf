"""Serve Django HTTP requests and aiodrf lifespan events."""

import os

from aiodrf_asgi_lifespan.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "project.settings")
application = get_asgi_application()
