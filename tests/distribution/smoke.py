"""Run from outside the source tree, using only an installed wheel and one extra."""

import asyncio
import importlib
import importlib.util
import sys
import threading
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path

import django
from django.conf import settings
from django.db import models
from django.test import override_settings

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
    "orjson": "orjson",
    "filter": "django_filters",
    "spectacular": "drf_spectacular",
    "codemod": "libcst",
    "opentelemetry": "opentelemetry",
    "tasks": "django_tasks",
    "whitenoise": "whitenoise",
    "granian": "granian",
    "lifespan": "aiodrf_asgi_lifespan",
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
    "opensearch",
):
    importlib.import_module(f"aiodrf.contrib.{extra}")
elif extra == "orjson":
    importlib.import_module("fastdrf.orjson.parsers")
    importlib.import_module("fastdrf.orjson.renderers")
elif extra == "lifespan":
    importlib.import_module("aiodrf_asgi_lifespan.asgi")
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
    invalid = Input(data={"value": "not-an-integer"})
    assert not await invalid.ais_valid()
    assert invalid.errors["value"][0].code == "invalid"

    from fastdrf import compiler

    from aiodrf import aio

    seen = []

    class Record(models.Model):  # noqa: DJ008 -- in-memory distribution fixture
        value = models.IntegerField()

        class Meta:
            app_label = "distribution_smoke"

    class Output(serializers.ModelSerializer):
        computed = serializers.SerializerMethodField()

        class Meta:
            model = Record
            fields = ["value", "computed"]

        def get_computed(self, instance):
            seen.append(threading.current_thread() is threading.main_thread())
            return instance.value + 1

    with override_settings(
        FASTDRF={
            "SERIALIZER_BACKEND": "python",
            "SERIALIZER_BACKEND_FALLBACK": "error",
            "DELEGATE_FIELDS": True,
        }
    ):
        output = Output(Record(value=3))
        assert compiler.report_details(output, "strict", "python").delegated == (
            "computed",
        )
        assert await aio.data(output) == {"value": 3, "computed": 4}
        assert seen == [False]

    if extra in ("pydantic", "orjson"):
        from io import BytesIO

        prefix = "PydanticJSON" if extra == "pydantic" else "ORJSON"
        parser = getattr(
            importlib.import_module(f"fastdrf.{extra}.parsers"), prefix + "Parser"
        )
        renderer = getattr(
            importlib.import_module(f"fastdrf.{extra}.renderers"), prefix + "Renderer"
        )
        from aiodrf.response import Response

        value = parser().parse(BytesIO(b'{"value":12}'))
        response = Response(value)
        response.accepted_renderer = renderer()
        response.accepted_media_type = "application/json"
        response.renderer_context = {}
        assert (await response.render()).content == b'{"value":12}'

    if extra in ("msgspec", "pydantic"):
        from fastdrf.inputs import recognize

        assert recognize(Input(data={"value": 12}), backend=extra) == {"value": 12}


asyncio.run(check())
print(f"Installed wheel + {extra}: passed")
