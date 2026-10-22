"""Loaded as a pytest plugin (``-p tests.bootstrap``) so it runs before collection."""

from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path

from dailyscraper.settings.environment import (
    ENVIRONMENT_VARIABLE,
    TEST_CONFIG_ROOT_VARIABLE,
    TEST_SESSION_ID_VARIABLE,
    TEST_WORKSPACE_VARIABLE,
    TESTING_ENVIRONMENT,
    validate_testing_workspace,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SESSION_ID = datetime.now().strftime("%y%m%d-%H%M%S") + f"-{os.getpid()}"
DEFAULT_WORKSPACE = (PROJECT_ROOT / ".test-artifacts" / SESSION_ID).resolve()
PROJECT_ENV_PREFIX = "DAILYSCRAPER_"
INHERITED_VARIABLES = frozenset({TEST_WORKSPACE_VARIABLE, TEST_CONFIG_ROOT_VARIABLE})

_owns_default_workspace = False


def _bootstrap_testing_environment() -> None:
    global _owns_default_workspace

    for name in tuple(os.environ):
        if name.startswith(PROJECT_ENV_PREFIX) and name not in INHERITED_VARIABLES:
            os.environ.pop(name, None)
    # Antivirus HTTPS scanners may point SSLKEYLOGFILE at a device path; uv's CPython
    # builds on Windows then abort in OpenSSL ("no OPENSSL_Applink") on the first TLS call.
    os.environ.pop("SSLKEYLOGFILE", None)
    os.environ[ENVIRONMENT_VARIABLE] = TESTING_ENVIRONMENT

    configured_workspace = str(os.environ.get(TEST_WORKSPACE_VARIABLE) or "").strip()
    _owns_default_workspace = not configured_workspace
    workspace = Path(configured_workspace or DEFAULT_WORKSPACE).expanduser().resolve()
    validate_testing_workspace(workspace, project_root=PROJECT_ROOT)
    workspace.mkdir(parents=True, exist_ok=True)
    os.environ[TEST_WORKSPACE_VARIABLE] = str(workspace)
    os.environ[TEST_SESSION_ID_VARIABLE] = SESSION_ID

    config_root = str(os.environ.get(TEST_CONFIG_ROOT_VARIABLE) or "").strip()
    if config_root and not Path(config_root).expanduser().resolve().is_relative_to(workspace):
        os.environ.pop(TEST_CONFIG_ROOT_VARIABLE, None)


_bootstrap_testing_environment()


def pytest_sessionfinish(session, exitstatus: int) -> None:
    if exitstatus != 0 or not _owns_default_workspace:
        return
    if session.config.getoption("keep_test_artifacts", default=False):
        return
    workspace = Path(os.environ[TEST_WORKSPACE_VARIABLE]).resolve()
    if workspace == DEFAULT_WORKSPACE:
        shutil.rmtree(workspace, ignore_errors=True)
