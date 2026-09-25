from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import CARTRIDGES_ROOT, PRIORITY_CARTRIDGES

REQUIRED_FILES = (
    "Dockerfile",
    "requirements.txt",
    "app/main.py",
    "app/security.py",
    "app/core/config.py",
    "app/core/sap_client.py",
    "app/config/connector.yaml",
    "app/config/entities.yaml",
    "app/config/knowledge_bits.yaml",
    "app/services/extraction_service.py",
    "app/services/catalog_service.py",
    "app/services/parquet_service.py",
    "app/services/runlog_service.py",
    "app/services/watermark_service.py",
    "app/mcp_server.py",
)


@pytest.mark.parametrize("cartridge", PRIORITY_CARTRIDGES)
def test_priority_cartridge_present(cartridge: str) -> None:
    cart_dir = CARTRIDGES_ROOT / cartridge
    assert cart_dir.is_dir(), f"missing cartridge {cartridge}"


@pytest.mark.parametrize("cartridge", PRIORITY_CARTRIDGES)
@pytest.mark.parametrize("rel_path", REQUIRED_FILES)
def test_priority_cartridge_files(cartridge: str, rel_path: str) -> None:
    p = CARTRIDGES_ROOT / cartridge / rel_path
    assert p.is_file(), f"{cartridge}: missing file {rel_path}"
