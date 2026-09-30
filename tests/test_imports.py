"""
Import-order guarantees, checked in a fresh interpreter: the test process
has imported everything already.
"""

import os
import subprocess
import sys
import textwrap

import pytest

POLICY_MODULE = """
from rest_framework.permissions import BasePermission

from aiodrf.response import Response  # noqa: F401
from aiodrf.utils import async_safe


@async_safe
class IsOwner(BasePermission):
    pass
"""


def run(code, *paths):
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([*paths, "src", "."])}
    env.pop("DJANGO_SETTINGS_MODULE", None)
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_policy_modules_named_in_drf_settings_can_import_aiodrf(tmp_path):
    # ``rest_framework.views`` imports every ``DEFAULT_*_CLASSES`` module
    # while its class body runs, so nothing such a module imports may import
    # ``rest_framework.views`` in turn.
    (tmp_path / "project_policies.py").write_text(POLICY_MODULE)
    result = run(
        """
        import django
        from django.conf import settings

        settings.configure(
            INSTALLED_APPS=["django.contrib.auth", "django.contrib.contenttypes", "rest_framework"],
            REST_FRAMEWORK={"DEFAULT_PERMISSION_CLASSES": ["project_policies.IsOwner"]},
        )
        django.setup()
        import project_policies
        from rest_framework.views import APIView

        assert APIView.permission_classes == [project_policies.IsOwner]
        """,
        str(tmp_path),
    )
    assert result.returncode == 0, result.stderr


AIODRF_DEFAULTS = [
    ("DEFAULT_PERMISSION_CLASSES", ["aiodrf.permissions.IsAuthenticated"]),
    ("DEFAULT_AUTHENTICATION_CLASSES", ["aiodrf.authentication.SessionAuthentication"]),
    ("DEFAULT_THROTTLE_CLASSES", ["aiodrf.throttling.AnonFixedWindowRateThrottle"]),
    ("DEFAULT_PAGINATION_CLASS", "aiodrf.pagination.PageNumberPagination"),
    (
        "DEFAULT_FILTER_BACKENDS",
        [
            "aiodrf.filters.SearchFilter",
            "aiodrf.contrib.django_filters.DjangoFilterBackend",
        ],
    ),
]


@pytest.mark.parametrize(("setting", "value"), AIODRF_DEFAULTS)
def test_aiodrf_policies_can_be_drf_defaults(setting, value):
    # The same guarantee for aiodrf's own policy modules.
    result = run(f"""
        import django
        from django.conf import settings

        settings.configure(
            INSTALLED_APPS=[
                "django.contrib.auth", "django.contrib.contenttypes", "rest_framework",
                "django_filters",
            ],
            REST_FRAMEWORK={{{setting!r}: {value!r}}},
        )
        django.setup()
        from rest_framework import generics
        from rest_framework.settings import api_settings

        assert getattr(api_settings, {setting!r})
    """)
    assert result.returncode == 0, result.stderr


def test_utils_import_needs_no_settings():
    result = run("from aiodrf.utils import async_safe, register_pure")
    assert result.returncode == 0, result.stderr


def test_field_copy_discovery_does_not_evaluate_lazy_settings():
    result = run("""
        from django.conf import settings
        from aiodrf.contrib.builtin.field_copy import plan_fields
        assert not settings.configured
        assert plan_fields({})() == {}
    """)
    assert result.returncode == 0, result.stderr


def test_pydantic_input_does_not_import_msgspec():
    result = run("""
        import sys
        from django.conf import settings
        settings.configure(REST_FRAMEWORK={})
        from rest_framework import serializers
        from aiodrf.contrib.inputs import recognize
        class Example(serializers.Serializer):
            value = serializers.IntegerField()
        assert recognize(Example(data={"value": 1}), backend="pydantic") == {"value": 1}
        assert "msgspec" not in sys.modules
    """)
    assert result.returncode == 0, result.stderr


def test_stream_schema_does_not_import_optional_serializer_backends():
    result = run("""
        import sys
        import django
        from django.conf import settings
        settings.configure(INSTALLED_APPS=["aiodrf"], REST_FRAMEWORK={})
        django.setup()
        from rest_framework import serializers
        from aiodrf.contrib.spectacular import StreamSchema
        class Item(serializers.Serializer):
            value = serializers.IntegerField()
        StreamSchema(Item)
        assert "msgspec" not in sys.modules
        assert "pydantic" not in sys.modules
    """)
    assert result.returncode == 0, result.stderr


def test_concurrent_serializer_import_needs_no_optional_packages():
    result = run("""
        import sys
        import django
        from django.conf import settings
        settings.configure(REST_FRAMEWORK={})
        django.setup()
        from aiodrf.contrib.builtin.concurrent import ConcurrentListSerializer
        assert ConcurrentListSerializer.max_concurrency == 4
        assert not {"msgspec", "pydantic", "drf_spectacular", "httpx"} & sys.modules.keys()
    """)
    assert result.returncode == 0, result.stderr


def test_aiodrf_aio_exposes_its_public_api_and_nothing_else():
    # The implementation lives in private submodules. Code, and tests that
    # patch it, must name the submodule that uses a name: patching a name on
    # the package would change nothing, so the package has none to patch.
    from aiodrf import aio

    names = {name for name in vars(aio) if not name.startswith("__")}
    assert names - {
        "_classify",
        "_common",
        "_loaded",
        "_represent",
        "_save",
        "_validate",
    } == set(aio.__all__)
