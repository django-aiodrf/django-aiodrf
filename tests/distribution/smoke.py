"""Run from outside the source tree, using only an installed wheel and one extra."""

import asyncio
import importlib
import importlib.util
import sys
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path

import django
from django.conf import settings

extra = sys.argv[1]
settings.configure(
    SECRET_KEY="distribution-smoke",
    INSTALLED_APPS=[
        "django.contrib.auth",
        "django.contrib.contenttypes",
        "rest_framework",
        "aiodrf",
    ],
)
django.setup()

import aiodrf  # noqa: E402
from aiodrf import serializers  # noqa: E402

assert Path(sys.prefix) in Path(aiodrf.__file__).parents
assert version("django-aiodrf") == aiodrf.__version__
assert files("aiodrf").joinpath("py.typed").is_file()
assert (
    files("aiodrf")
    .joinpath("management/commands/aiodrf_inspect_serializers.py")
    .is_file()
)

dependencies = {
    "msgspec": "msgspec",
    "pydantic": "pydantic",
    "filter": "django_filters",
    "spectacular": "drf_spectacular",
    "codemod": "libcst",
    "opentelemetry": "opentelemetry",
    "tasks": "django_tasks",
    "whitenoise": "whitenoise",
    "granian": "granian",
    "valkey": "django_valkey",
    "redis": "redis",
    "opensearch": "opensearchpy",
    "async-backend": "django_async_backend",
}
assert extra == "core" or extra in dependencies, extra
for name, module in dependencies.items():
    assert (importlib.util.find_spec(module) is not None) == (extra == name), (
        extra,
        module,
    )
if extra in (
    "msgspec",
    "pydantic",
    "spectacular",
    "opentelemetry",
    "whitenoise",
    "valkey",
    "redis",
    "opensearch",
):
    importlib.import_module(f"aiodrf.contrib.{extra}")
elif extra == "filter":
    importlib.import_module("aiodrf.filters")
elif extra == "granian":
    importlib.import_module("granian")
elif extra == "async-backend":
    importlib.import_module("aiodrf.contrib.async_backend")
elif extra == "codemod":
    from aiodrf.codemod import transform_source

    assert (
        "aiodrf.views"
        in transform_source("from rest_framework.views import APIView\n").code
    )


class Input(serializers.Serializer):
    value = serializers.IntegerField()


async def check():
    serializer = Input(data={"value": "12"})
    assert await serializer.ais_valid()
    assert serializer.validated_data == {"value": 12}
    if extra in ("msgspec", "pydantic"):
        from aiodrf.contrib.inputs import recognize

        assert recognize(Input(data={"value": 12}), backend=extra) == {"value": 12}


asyncio.run(check())
print(f"Installed wheel + {extra}: passed")
