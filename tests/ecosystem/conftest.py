import os

# django-tenants needs a project of its own (database engine, router,
# middleware): ``nox -s ecosystem_tenants`` with ``tests.ecosystem.tenants.settings``.
# django-prometheus too (``nox -s ecosystem_prometheus``), and
# django-mongodb-backend (``nox -s ecosystem_mongodb``).
SEPARATE = {
    "tenants": "tests.ecosystem.tenants.settings",
    "prometheus": "tests.ecosystem.prometheus.settings",
    "mongodb": "tests.ecosystem.mongodb.settings",
}
collect_ignore = [
    directory
    for directory, settings in SEPARATE.items()
    if os.environ.get("DJANGO_SETTINGS_MODULE") != settings
]
