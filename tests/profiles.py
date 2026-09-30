"""
The benchmark's tuned profile, for running a test session with it::

    AIODRF_TEST_PROFILE=tuned nox -s tests ecosystem ...

``tuned-drf-fallback`` is the same with ``SERIALIZER_BACKEND_FALLBACK="drf"``,
``tuned-python`` with ``SERIALIZER_BACKEND="python"``.

Every opt-in the profile selects is on; the browsable API stays a renderer,
so that tests of HTML pages keep their subject. ``DataResponse`` is chosen by
view code, not by a setting, and is not applied here. A test that verifies
another configuration declares it with ``pytest.mark.aiodrf_settings``
(``tests/conftest.py``).
"""

import os

TUNED_AIODRF = {
    "SERIALIZER_BACKEND": "msgspec",
    "SERIALIZER_BACKEND_PARITY": "strict",
    "SERIALIZER_BACKEND_FALLBACK": "error",
    "CACHE_SERIALIZER_FIELDS": True,
    "FIELD_COPY_MODE": "compiled",
    "REPRESENTATION_MODE": "inline",
    "REQUEST_THREADS": 32,
}
TUNED_RENDERERS = [
    "aiodrf.contrib.msgspec.renderers.MsgspecJSONRenderer",
    "rest_framework.renderers.BrowsableAPIRenderer",
]


PROFILES = {
    "tuned": TUNED_AIODRF,
    # The profile with the compiler's default fallback: serializers that do
    # not compile use DRF instead of raising.
    "tuned-drf-fallback": {**TUNED_AIODRF, "SERIALIZER_BACKEND_FALLBACK": "drf"},
    # The profile with output compiled without msgspec or pydantic.
    "tuned-python": {**TUNED_AIODRF, "SERIALIZER_BACKEND": "python"},
}


def apply(namespace):
    """Apply ``AIODRF_TEST_PROFILE`` to a settings module's ``globals()``."""
    profile = PROFILES.get(os.environ.get("AIODRF_TEST_PROFILE", ""))
    if profile is None:
        return
    namespace["AIODRF"] = {**namespace.get("AIODRF", {}), **profile}
    namespace["REST_FRAMEWORK"] = {
        **namespace.get("REST_FRAMEWORK", {}),
        "DEFAULT_RENDERER_CLASSES": TUNED_RENDERERS,
    }
