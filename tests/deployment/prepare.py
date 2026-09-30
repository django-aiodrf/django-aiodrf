"""Seed only the new database owned by the deployment harness."""

import json
from pathlib import Path

import django


def main():
    django.setup()
    from django.conf import settings
    from django.contrib.auth.models import User
    from django.core.management import call_command
    from django.db import connection
    from django.test import Client

    from tests.testapp.models import Author

    if not settings.DATABASES["default"]["NAME"].startswith("aiodrf_deploy_"):
        raise RuntimeError(
            "Refusing to seed a database not owned by the deployment harness"
        )
    call_command("migrate", verbosity=0, interactive=False)
    with connection.schema_editor() as editor:
        editor.create_model(Author)
    Author.objects.bulk_create([Author(name=f"author-{index}") for index in range(30)])
    user = User.objects.create_user(username="load-test")
    client = Client()
    client.force_login(user)
    path = Path(settings.CONFIG["directory"]) / "session.json"
    with path.open("x", encoding="utf-8") as destination:
        json.dump({"sessionid": client.cookies["sessionid"].value}, destination)
    connection.close()
    connection.close_pool()


if __name__ == "__main__":
    main()
