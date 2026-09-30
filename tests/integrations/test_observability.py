"""SDK instrumentation mutates Django; never install it in the parent process."""

import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    ("sdk", "transport", "case"),
    [
        (sdk, transport, "test_context_and_error_parity")
        for sdk in ("sentry", "otel")
        for transport in ("asgi", "wsgi")
    ]
    + [(sdk, "asgi", "test_disconnect") for sdk in ("sentry", "otel")]
    + [("otel", "asgi", "test_sampling_and_batch_export_failure")],
)
def test_observability_in_fresh_interpreter(sdk, transport, case, record_property):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-rx",
            f"tests/integrations/observability_case.py::{case}",
        ],
        env={**os.environ, "TEST_SDK": sdk, "TEST_TRANSPORT": transport},
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    record_property("child_result", result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
