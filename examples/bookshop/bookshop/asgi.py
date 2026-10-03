"""ASGI entry point for the bookshop application."""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bookshop.settings")

from aiodrf_asgi_lifespan.asgi import get_asgi_application

application = get_asgi_application()
