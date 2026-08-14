from __future__ import annotations

import os


def test_required_gate() -> None:
    assert os.environ.get("OMEGA_ADVERSARIAL_EXITSTATUS_ZERO") != "1"
