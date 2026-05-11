"""Make sure the audit cleared every mock / fake-data trace from SAP cartridges."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import ALL_SAP_CARTRIDGES, CARTRIDGES_ROOT

FORBIDDEN_TOKENS = (
    "MockEntity",
    "DummyClient",
    "RepliconClient",
    "from app.core.replicon_client",
)


def _iter_python_files(root: Path):
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


@pytest.mark.parametrize("cartridge", ALL_SAP_CARTRIDGES)
@pytest.mark.parametrize("token", FORBIDDEN_TOKENS)
def test_no_forbidden_tokens(cartridge: str, token: str) -> None:
    cart_dir = CARTRIDGES_ROOT / cartridge
    hits = []
    for path in _iter_python_files(cart_dir):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if token in text:
            hits.append(str(path.relative_to(CARTRIDGES_ROOT)))
    assert not hits, f"{cartridge}: {token} appears in {hits}"
