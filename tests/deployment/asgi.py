import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.deployment.settings")

from aiodrf.asgi import get_asgi_application
from tests.deployment.lifecycle import lifespan

application = get_asgi_application(lifespan=lifespan)
