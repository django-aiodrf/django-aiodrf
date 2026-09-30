"""
Schemathesis (a development tool, not a dependency of the project) generates
requests from the OpenAPI schema and sends them to the real ASGI application,
with its lifespan: no generated request may produce a server error, and the
responses the schema documents must match it.

Deterministic (``derandomize``) and small, so that it runs with the other
tests; the test database is the only one it writes to. Statuses the schema
does not document (DRF's 400/401/403/404, which drf-spectacular leaves out)
are not checked: that is drf-spectacular's contract, the same for DRF.
"""

from contextlib import asynccontextmanager

import httpx
import pytest
import schemathesis
from django.core.cache import cache
from hypothesis import HealthCheck, settings
from schemathesis.checks import not_a_server_error
from schemathesis.specs.openapi.checks import (
    content_type_conformance,
    response_schema_conformance,
)

from aiodrf.asgi import get_asgi_application
from bookshop.lifecycle import Resources

CHECKS = [not_a_server_error, response_schema_conformance, content_type_conformance]


@asynccontextmanager
async def lifespan():
    stock = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    async with httpx.AsyncClient(transport=stock, base_url="http://stock") as http:
        yield Resources(stock=http)


@pytest.fixture
def api_schema():
    # Loaded per test rather than at import: Schemathesis's ASGI test client
    # (starlette-testclient) leaves anyio memory streams unclosed, and only
    # these tests ignore that warning.
    return schemathesis.openapi.from_asgi(
        "/schema/?format=json", get_asgi_application(lifespan=lifespan)
    )


schema = schemathesis.pytest.from_fixture("api_schema")


@pytest.fixture
def library(transactional_db):
    from catalog.models import Author, Book

    cache.clear()  # the throttle counters
    author = Author.objects.create(name="Ursula K. Le Guin")
    Book.objects.create(sku="EARTHSEA", title="A Wizard of Earthsea", author=author)


@pytest.fixture
def editor(library):
    from django.contrib.auth.models import User
    from rest_framework.authtoken.models import Token

    user = User.objects.create_user("editor", password="unused")
    return {"Authorization": f"Token {Token.objects.create(user=user).key}"}


def check(case, headers):
    cache.clear()  # the anonymous rate would answer 429 after 60 examples
    response = case.call(headers=headers)
    case.validate_response(response, checks=CHECKS)


FUZZ = settings(
    max_examples=25,
    derandomize=True,
    database=None,
    deadline=None,
    # Schemathesis discards the generated requests the schema does not
    # admit, many for a write with path and body constraints (PUT
    # /books/{id}/): the checks concern generation speed, not the API.
    suppress_health_check=[
        HealthCheck.function_scoped_fixture,
        HealthCheck.too_slow,
        HealthCheck.filter_too_much,
    ],
)
# The ASGI test client Schemathesis uses (starlette-testclient) leaves anyio
# memory streams unclosed; nothing of the project's is involved.
unclosed_streams = pytest.mark.filterwarnings(
    "ignore::ResourceWarning", "ignore::pytest.PytestUnraisableExceptionWarning"
)


@unclosed_streams
@pytest.mark.usefixtures("library")
@schema.parametrize()
@FUZZ
def test_anonymous_requests(case):
    check(case, {})


@unclosed_streams
@schema.parametrize()
@FUZZ
def test_requests_with_a_token(case, editor):
    check(case, editor)
