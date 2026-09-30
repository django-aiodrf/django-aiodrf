"""Explicit remote telemetry; credentials are never supplied by the example."""

import os

from .settings import *  # noqa: F403

APITALLY_MIDDLEWARE = {
    "client_id": os.environ["EXAMPLE_APITALLY_CLIENT_ID"],
    "env": os.environ.get("EXAMPLE_APITALLY_ENV", "dev"),
}
MIDDLEWARE = ["apitally.django.ApitallyMiddleware", *MIDDLEWARE]  # noqa: F405
