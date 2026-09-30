"""OpenAPI preprocessing hooks for supported HTTP operations."""

from typing import Any

__all__ = ["preprocess_exclude_query_method"]


def preprocess_exclude_query_method(endpoints: list[Any], **kwargs: Any) -> list[Any]:
    """
    drf-spectacular preprocessing hook that leaves QUERY handlers out of the
    schema (``SPECTACULAR_SETTINGS["PREPROCESSING_HOOKS"]``).

    OpenAPI 3.0 and 3.1 have no QUERY operation, and drf-spectacular fails
    on a view that implements one: it builds a request for every method with
    DRF's ``APIRequestFactory``, which has no ``query()``. The view's other
    methods are documented as before.
    """
    return [endpoint for endpoint in endpoints if endpoint[2] != "QUERY"]
