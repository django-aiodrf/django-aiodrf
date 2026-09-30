"""Shared classification and database cleanup for tests and example projects."""

import gc

import pytest
from django.db.backends.base.base import BaseDatabaseWrapper

# Explicitly reviewed, service-free modules. New tests are integration tests
# unless they opt in with pytest.mark.unit; collection never infers purity.
UNIT_MODULES = frozenset(
    {
        "test_api_inventory.py",
        "test_docs.py",
        "test_examples_catalogue.py",
        "test_inputs.py",
        "test_compiler.py",
        "test_threads.py",
        "test_codemod.py",
        "test_naming_check.py",
        "test_settings.py",
    }
)


def pytest_configure(config):
    """Register markers in standalone example configurations as well."""
    config.addinivalue_line("markers", "unit: isolated tests without external services")
    config.addinivalue_line(
        "markers", "integration: request, database or service contracts"
    )


def pytest_collection_modifyitems(items):
    """Keep the dev branch's isolated tests separate from integration suites."""
    for item in items:
        if not item.get_closest_marker("unit") and not item.get_closest_marker(
            "integration"
        ):
            is_unit = (
                item.path.parent.name == "tests" and item.path.name in UNIT_MODULES
            )
            item.add_marker(pytest.mark.unit if is_unit else pytest.mark.integration)


def pytest_sessionfinish(session):
    # Django's SQLite backend ignores close() for an in-memory database, which
    # closing would destroy, in every thread that used it: worker threads'
    # connections were finalized open at interpreter exit (a ResourceWarning).
    # The test databases are gone by now; close the connections themselves.
    for wrapper in gc.get_objects():
        if (
            # type(): isinstance() would set up lazy objects (the admin site).
            issubclass(type(wrapper), BaseDatabaseWrapper)
            and wrapper.vendor == "sqlite"
            and wrapper.connection is not None
            and wrapper.is_in_memory_db()
        ):
            wrapper.connection.close()
            wrapper.connection = None
