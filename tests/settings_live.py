"""A file-backed SQLite test database for real-server connection lifetimes."""

import os

from tests.settings import *  # noqa: F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
        "TEST": {"NAME": os.environ["AIODRF_LIVE_SQLITE"]},
    }
}
