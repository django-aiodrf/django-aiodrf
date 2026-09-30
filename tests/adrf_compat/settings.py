"""The test project with aiodrf's adrf compatibility layer (``nox -s adrf_compat``)."""

from tests.settings import *  # noqa: F403

AIODRF = {"ADRF_COMPAT": True}
ROOT_URLCONF = "tests.adrf_compat.project"

# ``AIODRF_TEST_PROFILE=tuned``: the benchmark's tuned profile (tests/profiles.py).
from tests.profiles import apply as _apply_profile  # noqa: E402

_apply_profile(globals())
