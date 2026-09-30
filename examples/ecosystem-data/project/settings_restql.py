"""Isolate the legacy pyPEG2 dependency from the warning-clean default profile."""

from .settings import *  # noqa: F403

ROOT_URLCONF = "project.urls_restql"
