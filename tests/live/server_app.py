"""CLI factory for a server-owned, disposable ASGI compatibility application."""

import os

from tests.live.resource_app import application


def create_application():
    """Use only paths explicitly supplied by the subprocess test harness."""
    return application(
        os.environ["AIODRF_SERVER_DATABASE"],
        os.environ["AIODRF_SERVER_JOURNAL"],
        initialize_database=False,
    )


if __name__ == "__main__":
    # Uvicorn invokes factories on its loop. Seed before starting any server,
    # rather than doing schema writes from an ASGI import/factory callback.
    application(
        os.environ["AIODRF_SERVER_DATABASE"], os.environ["AIODRF_SERVER_JOURNAL"]
    )
