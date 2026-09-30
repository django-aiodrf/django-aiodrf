"""Migrate a local example once, then replace this process with its ASGI server."""

import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    project = "bookshop" if Path.cwd().name == "bookshop" else "project"
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", f"{project}.settings")
    if len(sys.argv) > 1:
        # Compose exec/run commands do not repeat migrations or start a server.
        os.execvp(sys.argv[1], sys.argv[1:])  # noqa: S606 -- explicit container command.
    migration = "migrate_schemas" if Path.cwd().name == "tenancy" else "migrate"
    subprocess.run(  # noqa: S603 -- fixed local management command.
        [sys.executable, "manage.py", migration, "--noinput"], check=True
    )
    if os.environ.get("EXAMPLE_CACHE") == "database":
        # Django skips an existing cache table.
        subprocess.run([sys.executable, "manage.py", "createcachetable"], check=True)
    os.execv(  # noqa: S606 -- the current interpreter, no shell expansion.
        sys.executable,
        [
            sys.executable,
            "-m",
            "uvicorn",
            f"{project}.asgi:application",
            "--host",
            "0.0.0.0",  # noqa: S104 -- container only; Compose publishes on loopback.
            "--port",
            "8000",
            "--lifespan",
            "on",
        ],
    )


if __name__ == "__main__":
    main()
