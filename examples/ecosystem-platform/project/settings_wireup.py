"""Vendor-owned dependency injection is opt-in and scoped to this application."""

from wireup.integration.django import WireupSettings

from .settings import *  # noqa: F403

INSTALLED_APPS = [*INSTALLED_APPS, "wireup.integration.django"]  # noqa: F405
MIDDLEWARE = ["wireup.integration.django.wireup_middleware", *MIDDLEWARE]  # noqa: F405
WIREUP = WireupSettings(injectables=["demo.services"])
