"""Explicit WSGI-oriented WhiteNoise comparison; the default profile uses ASGI."""

from .settings import *  # noqa: F403
from .settings import MIDDLEWARE as BASE_MIDDLEWARE

MIDDLEWARE = [
    "whitenoise.middleware.WhiteNoiseMiddleware"
    if name == "servestatic.middleware.ServeStaticMiddleware"
    else name
    for name in BASE_MIDDLEWARE
]
