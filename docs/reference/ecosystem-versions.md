# Ecosystem test versions

The [Django/DRF integration requirements](../../requirements/ecosystem/requirements.txt)
and [framework-neutral support requirements](../../requirements/support/requirements.txt)
list the minimum versions (`>=`) of the packages aiodrf is tested with. Both are
installed together in the integration tests.

The [ecosystem guide](../guides/ecosystem.md) describes the tested scenarios of
each package. The [example inventory](../../examples/ECOSYSTEM.md) identifies
runnable demonstrations and the services they need. Passing these tests does
not make aiodrf responsible for every feature or configuration of a package.

The [server requirements](../../requirements/servers/requirements.txt) list the
minimum versions of the ASGI servers aiodrf is tested with. See
[ASGI server selection](../guides/web-servers.md) for the exercised protocols
and platform limits.

Core Python/Django/DRF versions are covered by the
[version matrix](../guides/releasing.md#supported-versions). Packages that
need a specific Django version or service (the native PostgreSQL backend,
MongoDB, django-tenants, django-prometheus) are tested with their own versions,
listed in the [ecosystem guide](../guides/ecosystem.md), and each example
declares its own dependencies.
