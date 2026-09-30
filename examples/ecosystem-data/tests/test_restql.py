"""The optional RESTQL profile exposes its upstream parser warning explicitly."""

import importlib
import warnings

import pytest
from demo.models import Author, Book, Limits
from django.conf import settings


@pytest.mark.django_db(transaction=True)
async def test_restql_field_selection(client):
    if settings.ROOT_URLCONF != "project.urls_restql":
        pytest.skip("Select project.settings_restql and install the restql extra")
    # pyPEG2 emits SyntaxWarning on a cold source import, not on every import.
    # Record only that known compile-time diagnostic; reject unrelated warnings.
    with warnings.catch_warnings(record=True) as diagnostics:
        warnings.simplefilter("always", SyntaxWarning)
        importlib.import_module("demo.restql")
    assert all(
        warning.category is SyntaxWarning
        and "invalid escape sequence" in str(warning.message)
        and "pypeg2" in warning.filename
        for warning in diagnostics
    )
    author = await Author.objects.acreate(name="Ursula")
    await Book.objects.acreate(title="Earthsea", author=author, limits=Limits(rate=10))
    response = await client.get("/restql/", params={"query": "{title}"})
    assert response.status_code == 200
    assert response.json() == [{"title": "Earthsea"}]
