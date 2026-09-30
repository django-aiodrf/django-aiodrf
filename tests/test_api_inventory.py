"""The API inventory of docs/guides/releasing.md against the modules' ``__all__``."""

from pathlib import Path

import pytest

from aiodrf import utils
from aiodrf.contrib import convert

INVENTORY = (Path(__file__).parent.parent / "docs/guides/releasing.md").read_text()
EXTENSION_ROW = next(
    line for line in INVENTORY.splitlines() if line.startswith("| Extension API")
)
INTERNAL_ROW = next(
    line for line in INVENTORY.splitlines() if line.startswith("| Internal")
)


def test_the_extension_api_of_utils_is_the_documented_one():
    assert set(utils.__all__) == {
        "async_safe",
        "count_hops",
        "register_pure",
        "register_pure_method",
        "run_sync",
    }
    for name in utils.__all__:
        assert f"`{name}`" in EXTENSION_ROW, name


@pytest.mark.parametrize(
    "name",
    ["Impl", "resolve_pair", "definer", "class_cache", "bridge_base", "call_pair"],
)
def test_internal_names_stay_importable_outside_all(name):
    assert name not in utils.__all__
    assert hasattr(utils, name)


def test_the_converters_description_is_internal():
    for name in ("Schema", "Spec", "T"):
        assert name not in convert.__all__
        assert hasattr(convert, name)
        assert f"`{name}`" in INTERNAL_ROW
    assert {"from_pydantic", "from_msgspec", "from_serializer", "to_drf"} <= set(
        convert.__all__
    )


def test_the_decorators_module_exports_drfs_decorators():
    from rest_framework import decorators as drf

    from aiodrf import decorators

    for name in (
        "action",
        "authentication_classes",
        "parser_classes",
        "permission_classes",
        "renderer_classes",
        "schema",
        "throttle_classes",
        # DRF 3.17+
        "content_negotiation_class",
        "metadata_class",
        "versioning_class",
    ):
        if hasattr(drf, name):
            assert name in decorators.__all__, name
            assert getattr(decorators, name) is getattr(drf, name)
