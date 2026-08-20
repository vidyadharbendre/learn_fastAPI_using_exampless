"""Shared pytest fixtures.

Each day lives in a folder whose name starts with a digit, so it is not an
importable Python package name. `day_module` puts the day's folder on `sys.path`
just long enough to import it — which keeps the day folders readable for humans
(`01_environment_setup_and_first_api`) without fighting Python's import rules.
"""

import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

DAY_DIRS = sorted(
    p for p in REPO_ROOT.iterdir() if p.is_dir() and p.name[:2].isdigit()
)


def import_from_day(day_dir_name: str, module: str):
    """Import `module` from the given day folder."""
    day_path = REPO_ROOT / day_dir_name
    inserted = False
    if str(day_path) not in sys.path:
        sys.path.insert(0, str(day_path))
        inserted = True
    try:
        return importlib.import_module(module)
    finally:
        if inserted:
            sys.path.remove(str(day_path))


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def day_dirs() -> list[Path]:
    return DAY_DIRS


@pytest.fixture()
def day01_client():
    """A TestClient for the Day 01 app.

    `TestClient` calls the app in-process through ASGI — no server, no socket,
    no port. That is why the whole suite runs in well under a second.
    """
    from fastapi.testclient import TestClient

    main = import_from_day("01_environment_setup_and_first_api", "shelfspace.main")
    with TestClient(main.create_app()) as client:
        yield client
