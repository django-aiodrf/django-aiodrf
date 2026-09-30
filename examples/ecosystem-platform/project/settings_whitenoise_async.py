"""Dual-mode WhiteNoise adapter; ASGI file bodies remain buffered by Django."""

from .settings_whitenoise import *  # noqa: F403

MIDDLEWARE = [
    "aiodrf.contrib.whitenoise.whitenoise_middleware"
    if item == "whitenoise.middleware.WhiteNoiseMiddleware"
    else item
    for item in MIDDLEWARE  # noqa: F405
]
