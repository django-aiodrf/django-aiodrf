import pytest
from django.conf import settings
from django.core.cache import cache
from django.db import connections
from django.test import override_settings

from aiodrf.utils import run_sync


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _aiodrf_settings(request):
    """
    The ``AIODRF`` values a test verifies (``pytest.mark.aiodrf_settings``),
    over those of the run, so that a run with another profile
    (``AIODRF_TEST_PROFILE``) still tests them.
    """
    marker = request.node.get_closest_marker("aiodrf_settings")
    if marker is None:
        yield
        return
    with override_settings(AIODRF={**getattr(settings, "AIODRF", {}), **marker.kwargs}):
        yield


@pytest.fixture
async def worker_connections(transactional_db):
    """Close connections owned by direct async ORM calls before DB teardown."""
    try:
        yield
    finally:
        await run_sync(connections.close_all)()
