"""The tracing contrib against the real SDK, each case in a fresh interpreter."""

import os
import subprocess
import sys

import pytest

CASES = [
    "test_phases_are_children_of_their_own_server_span",
    "test_an_inherited_synchronous_check_runs_in_its_span",
    "test_a_cancelled_handler_ends_its_span",
    "test_nothing_is_recorded_when_sampling_is_off",
    "test_without_a_configured_provider_the_phases_cost_nothing_visible",
]


@pytest.mark.parametrize("case", CASES)
def test_tracing_in_fresh_interpreter(case, record_property):
    pytest.importorskip("opentelemetry.sdk")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-rx",
            f"tests/integrations/tracing_case.py::{case}",
        ],
        env={**os.environ, "TEST_SDK": "otel", "TEST_TRANSPORT": "asgi"},
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    record_property("child_result", result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
