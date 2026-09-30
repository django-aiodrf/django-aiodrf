"""ASGI entry point for the tasks django6 application."""

import os

from aiodrf.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "project.settings")
application = get_asgi_application()
