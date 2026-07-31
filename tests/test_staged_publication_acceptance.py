from __future__ import annotations

from pathlib import Path

import pytest

from tests.staged_publication_canaries import CANARIES


@pytest.mark.parametrize("canary", CANARIES, ids=CANARIES)
def test_staged_publication_acceptance_canary_exists(canary: str) -> None:
    sources = (
        Path(__file__).with_name("staged_publication_canary_impl.py"),
        Path(__file__).with_name("staged_publication_upgrade_canaries.py"),
    )
    assert any(
        f"def {canary}(" in source.read_text(encoding="utf-8") for source in sources
    )
