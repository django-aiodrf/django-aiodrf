"""Django application registration and startup hooks."""

from importlib.util import find_spec
from types import ModuleType

from django.apps import AppConfig


class AioDRFConfig(AppConfig):
    name = "aiodrf"
    verbose_name = "aiodrf"

    def __init__(self, app_name: str, app_module: ModuleType | None) -> None:
        super().__init__(app_name, app_module)
        # Before any app's models (and after them, its views) are imported:
        # ready() would be too late for an app whose models import adrf.
        from aiodrf.settings import aiodrf_settings

        if aiodrf_settings.ADRF_COMPAT:
            from aiodrf.contrib.adrf_compat import install

            install()

    def ready(self) -> None:
        from django.apps import apps
        from django.core import checks as django_checks

        from aiodrf import checks  # noqa: F401 -- registers system checks
        from aiodrf.settings import aiodrf_settings

        # The FASTDRF settings aiodrf reads, checked as when the fastdrf
        # application is installed.
        if not apps.is_installed("fastdrf"):
            from fastdrf.checks import check_integrations, check_settings

            django_checks.register(check_settings, django_checks.Tags.compatibility)
            django_checks.register(check_integrations, django_checks.Tags.compatibility)

        if aiodrf_settings.MONKEYPATCHES:
            from aiodrf.contrib.monkeypatches import apply

            apply(*aiodrf_settings.MONKEYPATCHES)

        # Only the package being absent is fine; an error inside it is not.
        if find_spec("drf_spectacular") is not None:
            from fastdrf import spectacular  # noqa: F401 -- schema serializers

            from aiodrf.contrib.spectacular import extensions  # noqa: F401
