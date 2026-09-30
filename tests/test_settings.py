import os
from unittest import mock

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import connections
from django.test import override_settings
from rest_framework.permissions import DjangoModelPermissions

from aiodrf import checks
from aiodrf.settings import aiodrf_settings
from aiodrf.utils import is_pure


@pytest.mark.aiodrf_settings(REPRESENTATION_MODE="thread")
def test_invalid_choice_is_rejected_on_every_access():
    with override_settings(AIODRF={"REPRESENTATION_MODE": "bogus"}):
        for _ in range(3):
            with pytest.raises(ImproperlyConfigured, match="REPRESENTATION_MODE"):
                getattr(aiodrf_settings, "REPRESENTATION_MODE")  # noqa: B009 -- raises
    assert aiodrf_settings.REPRESENTATION_MODE == "thread"


def test_removed_choice_explains_the_migration():
    with (
        override_settings(AIODRF={"VALIDATION_UNKNOWN": "optimistic"}),
        pytest.raises(ImproperlyConfigured, match='Use "thread", or "inline"'),
    ):
        getattr(aiodrf_settings, "VALIDATION_UNKNOWN")  # noqa: B009 -- raises


def test_pure_policies_are_imported():
    permission = DjangoModelPermissions()
    assert not is_pure(permission, "has_permission")
    # The path a class is imported from need not be the module defining it.
    with override_settings(
        AIODRF={"PURE_POLICIES": ["aiodrf.permissions.DjangoModelPermissions"]}
    ):
        assert is_pure(permission, "has_permission")
    with override_settings(AIODRF={"PURE_POLICIES": [DjangoModelPermissions]}):
        assert is_pure(permission, "has_permission")
    # ``override_settings`` reloads the set.
    assert not is_pure(permission, "has_permission")


def test_mistyped_pure_policy_is_an_error():
    with (
        override_settings(
            AIODRF={"PURE_POLICIES": ["aiodrf.permissions.NoSuchPermission"]}
        ),
        pytest.raises(ImportError, match="PURE_POLICIES"),
    ):
        is_pure(DjangoModelPermissions(), "has_permission")


def check_ids(check):
    return [message.id for message in check(app_configs=None)]


def test_settings_check():
    assert check_ids(checks.check_settings) == []
    with override_settings(
        AIODRF={
            "VALIDATION_UNKNOWN": "sometimes",
            "INLINE_RENDERERS": ["rest_framework.renderers.NoSuchRenderer"],
            "FETCH_MODES": "raise",
        }
    ):
        assert check_ids(checks.check_settings) == [
            "aiodrf.E001",
            "aiodrf.E002",
            "aiodrf.W003",
        ]


def test_atomic_requests_check():
    assert check_ids(checks.check_atomic_requests) == []
    # ``override_settings(DATABASES=...)`` does not reach ``connections.settings``.
    with mock.patch.dict(connections.settings["default"], {"ATOMIC_REQUESTS": True}):
        assert check_ids(checks.check_atomic_requests) == ["aiodrf.W002"]


def test_async_unsafe_check():
    assert check_ids(checks.check_async_unsafe) == []
    with mock.patch.dict(os.environ, {"DJANGO_ALLOW_ASYNC_UNSAFE": "1"}):
        assert check_ids(checks.check_async_unsafe) == ["aiodrf.W001"]
        with override_settings(DEBUG=True):
            assert check_ids(checks.check_async_unsafe) == []


@pytest.mark.aiodrf_settings(REPRESENTATION_MODE="thread")
def test_a_value_read_before_a_reload_is_not_cached_after_it():
    # The interleaving that a free-threaded run hit: ``__getattr__`` reads the
    # old value, ``reload()`` runs, the old value is published. Here the
    # reload happens inside the read, deterministically.
    from aiodrf.settings import DEFAULTS, IMPORT_STRINGS, AioDRFSettings

    class ReadDuringReload(dict):
        def get(self, key, default=None):
            value = super().get(key, default)
            instance.reload()
            return value

    instance = AioDRFSettings(
        ReadDuringReload({"REPRESENTATION_MODE": "inline"}), DEFAULTS, IMPORT_STRINGS
    )
    # The in-flight read finishes with what it read ...
    assert instance.REPRESENTATION_MODE == "inline"
    # ... but did not cache it: after the reload the settings are Django's.
    assert instance.REPRESENTATION_MODE == "thread"
    assert (
        "REPRESENTATION_MODE" not in instance._cached_attrs
        or instance.REPRESENTATION_MODE == "thread"
    )


@pytest.mark.parametrize("value", [None, [], 3, "invalid"])
def test_malformed_settings_mapping_is_reported(value):
    with override_settings(AIODRF=value):
        assert check_ids(checks.check_settings) == ["aiodrf.E003"]
        with pytest.raises(ImproperlyConfigured, match="AIODRF"):
            getattr(aiodrf_settings, "VALIDATION_UNKNOWN")  # noqa: B009 -- raises


@pytest.mark.parametrize(
    ("name", "value", "error"),
    [
        ("INLINE_RENDERERS", None, "aiodrf.E006"),
        ("PURE_POLICIES", 1, "aiodrf.E006"),
        ("PURE_POLICIES", [{}], "aiodrf.E006"),
        ("PURE_POLICIES", ["builtins.len"], "aiodrf.E007"),
        ("VALIDATION_UNKNOWN", [], "aiodrf.E001"),
        ("SERIALIZER_BACKEND", {}, "aiodrf.E001"),
        ("ATOMIC_SAVE", 1, "aiodrf.E006"),
    ],
)
def test_malformed_setting_values_are_reported(name, value, error):
    with override_settings(AIODRF={name: value}):
        for _ in range(2):
            assert check_ids(checks.check_settings) == [error]
            with pytest.raises(ImproperlyConfigured, match=name):
                getattr(aiodrf_settings, name)


@pytest.mark.parametrize("mode", ["peers", "raise"])
def test_fetch_mode_without_django_support_is_reported(mode):
    with override_settings(AIODRF={"FETCH_MODE": mode}):
        with mock.patch.object(checks, "DJANGO_HAS_FETCH_MODES", False):
            assert check_ids(checks.check_settings) == ["aiodrf.W009"]
        with mock.patch.object(checks, "DJANGO_HAS_FETCH_MODES", True):
            assert check_ids(checks.check_settings) == []
    with mock.patch.object(checks, "DJANGO_HAS_FETCH_MODES", False):
        assert check_ids(checks.check_settings) == []


def test_every_setting_has_a_validator():
    from aiodrf.settings import CHOICES, DEFAULTS, VALIDATORS

    assert set(VALIDATORS) == set(DEFAULTS)
    for name, default in DEFAULTS.items():
        assert VALIDATORS[name](name, default) is None, name
    assert set(CHOICES) <= set(VALIDATORS)
