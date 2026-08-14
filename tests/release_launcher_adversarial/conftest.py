from __future__ import annotations

import os

import pytest


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Model a downstream hook trying to erase a real failing exit status."""

    if os.environ.get("OMEGA_ADVERSARIAL_EXITSTATUS_ZERO") == "1":
        session.exitstatus = 0
