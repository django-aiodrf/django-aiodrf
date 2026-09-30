"""Report serializer backend eligibility without serving requests."""

import json
from argparse import ArgumentParser
from collections.abc import Callable, Sequence
from importlib.util import find_spec
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.utils.module_loading import import_string
from rest_framework.schemas.generators import EndpointEnumerator
from rest_framework.serializers import BaseSerializer

from aiodrf.contrib.compiler import Eligibility, report_details
from aiodrf.settings import aiodrf_settings
from aiodrf.utils import user_defines


class Command(BaseCommand):
    help = (
        "List the serializers used by API views and what the msgspec/pydantic "
        "serializer backend can take over for each: its output, its input, or "
        "neither, with the reason."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--format", choices=["text", "json"], default="text")
        parser.add_argument(
            "--serializer",
            action="append",
            default=[],
            dest="serializers",
            help="Inspect this serializer class instead of discovering endpoints; repeatable dotted path.",
        )
        parser.add_argument(
            "--parity",
            choices=["strict", "fast"],
            default=aiodrf_settings.SERIALIZER_BACKEND_PARITY,
        )
        parser.add_argument(
            "--backend",
            choices=["msgspec", "pydantic", "python"],
            default=None,
            help="Defaults to AIODRF['SERIALIZER_BACKEND'], or msgspec.",
        )

    def handle(
        self, *args: Any, parity: str, backend: str | None, **options: Any
    ) -> None:
        if backend is None:
            configured = aiodrf_settings.SERIALIZER_BACKEND
            backend = configured if configured != "drf" else "msgspec"
        report_input: Callable[..., Any] | None = None
        # The python backend needs no package and compiles output only.
        installed = backend == "python" or find_spec(backend) is not None
        if backend == "python":

            def report_input(serializer: Any, backend: str) -> Eligibility:
                return Eligibility(
                    "output_only", "the python backend compiles output only"
                )

        elif installed:
            from aiodrf.contrib.inputs import report_input_details as report_input
        # Without the backend only what keeps a serializer on DRF is known.
        missing = Eligibility("backend_not_installed", f"{backend} is not installed")

        records: list[dict[str, Any]] = []
        found, not_inspected = self._serializers(options["serializers"])
        for serializer_class, usages in found.items():
            name = f"{serializer_class.__module__}.{serializer_class.__qualname__}"
            # One serializer's failure is its record's: DRF builds fields
            # lazily, so one needing a request may only fail in the analysis.
            try:
                serializer = serializer_class()
            except Exception as exc:  # noqa: BLE001 -- serializers needing context, bare schema classes
                reason = f"could not be instantiated: {exc}"
                records.append(
                    self._not_inspected(name, usages, reason, exc, options["format"])
                )
                continue
            try:
                output = report_details(
                    serializer, parity, backend if installed else None
                )
                directions = {
                    "output": output if installed or not output.eligible else missing,
                    "input": (
                        report_input(serializer, backend=backend)
                        if report_input is not None
                        else missing
                    ),
                }
            except Exception as exc:  # noqa: BLE001 -- fields needing context, the project's hooks
                reason = f"could not be analyzed: {type(exc).__name__}: {exc}"
                records.append(
                    self._not_inspected(name, usages, reason, exc, options["format"])
                )
                continue
            records.append(
                {
                    "serializer": name,
                    "inspected": True,
                    "backend": backend,
                    "parity": parity,
                    "scope": "instance",
                    "usages": usages,
                    "directions": {
                        direction: {
                            "eligible": result.eligible,
                            "code": result.code,
                            "reason": result.reason,
                        }
                        for direction, result in directions.items()
                    },
                }
            )
            if options["format"] == "text":
                self.stdout.write(name)
                for direction, result in directions.items():
                    self._line(direction, result.reason)
        # Endpoints whose serializer is not declared: what serves them is
        # only known at request time, and this report says nothing about it.
        for usage, reason in not_inspected:
            records.append(
                {
                    "serializer": None,
                    "inspected": False,
                    "usages": [usage],
                    "reason": reason,
                }
            )
        if options["format"] == "text" and not_inspected:
            self.stdout.write("Not inspected:")
            for usage, reason in not_inspected:
                self.stdout.write(f"  {usage['method']} {usage['path']}: {reason}")
        if options["format"] == "json":
            self.stdout.write(json.dumps(records, indent=2))

    def _serializers(
        self, paths: Sequence[str]
    ) -> tuple[dict[Any, Any], list[tuple[dict[str, Any], str]]]:
        found: dict[Any, Any] = {}
        not_inspected: list[tuple[dict[str, Any], str]] = []
        if paths:
            for dotted_path in paths:
                try:
                    serializer_class = import_string(dotted_path)
                except ImportError as exc:
                    raise CommandError(
                        f"Cannot import serializer {dotted_path!r}: {exc}"
                    ) from exc
                if not isinstance(serializer_class, type) or not issubclass(
                    serializer_class, BaseSerializer
                ):
                    raise CommandError(
                        f"{dotted_path!r} must name a DRF serializer class."
                    )
                found.setdefault(serializer_class, [])
            return found, not_inspected
        for path, method, callback in EndpointEnumerator().get_api_endpoints():
            view_class = getattr(callback, "cls", None)
            usage = {
                "path": path,
                "method": method,
                "action": getattr(callback, "actions", {}).get(method.lower()),
            }
            # DRF routers put @action(serializer_class=...) in initkwargs.
            # Inspect declarations only; never call get_serializer_class().
            serializer_class = getattr(callback, "initkwargs", {}).get(
                "serializer_class", getattr(view_class, "serializer_class", None)
            )
            # Either member of the pair may choose the serializer.
            dynamic = view_class is not None and next(
                (
                    name
                    for name in ("get_serializer_class", "aget_serializer_class")
                    if user_defines(view_class, name)
                ),
                None,
            )
            if serializer_class is not None:
                if dynamic:
                    usage["note"] = (
                        f"{dynamic}() may choose another serializer at request time"
                    )
                found.setdefault(serializer_class, []).append(usage)
            elif dynamic:
                not_inspected.append(
                    (usage, f"the view chooses its serializer in {dynamic}()")
                )
            elif hasattr(view_class, "get_serializer_class"):
                not_inspected.append((usage, "the view declares no serializer_class"))
            else:
                not_inspected.append(
                    (
                        usage,
                        "not a generic view: serializers its handlers use are not declared",
                    )
                )
        return found, not_inspected

    def _not_inspected(
        self,
        name: str,
        usages: list[dict[str, Any]],
        reason: str,
        exc: Exception,
        output_format: str,
    ) -> dict[str, Any]:
        if output_format == "text":
            self.stdout.write(f"{name}: {reason}")
        return {
            "serializer": name,
            "inspected": False,
            "usages": usages,
            "reason": reason,
            "error": str(exc),
        }

    def _line(self, direction: str, reason: str | None) -> None:
        if reason is None:
            self.stdout.write(self.style.SUCCESS(f"  {direction:<7}compiled"))
        else:
            self.stdout.write(f"  {direction:<7}DRF: {reason}")
