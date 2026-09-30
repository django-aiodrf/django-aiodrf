"""Request identifiers and structured logs, without exporting request bodies."""

import structlog

from .settings import *  # noqa: F403
from .settings import MIDDLEWARE as BASE_MIDDLEWARE

INSTALLED_APPS = [*INSTALLED_APPS, "django_structlog", "drf_api_logger"]  # noqa: F405
MIDDLEWARE = [
    *BASE_MIDDLEWARE,
    "log_request_id.middleware.RequestIDMiddleware",
    "django_structlog.middlewares.RequestMiddleware",
    "drf_api_logger.middleware.api_logger_middleware.APILoggerMiddleware",
]
LOG_REQUEST_ID_HEADER = "HTTP_X_REQUEST_ID"
REQUEST_ID_RESPONSE_HEADER = "X-Request-ID"
DRF_API_LOGGER_SIGNAL = True
DRF_API_LOGGER_DATABASE = False
DRF_API_LOGGER_SKIP_KEYS = ["password", "token", "authorization", "cookie"]
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.JSONRenderer(),
    ]
)
