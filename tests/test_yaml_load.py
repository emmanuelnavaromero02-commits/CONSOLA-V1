from __future__ import annotations

import pytest
import yaml

from tests.conftest import CARTRIDGES_ROOT, PRIORITY_CARTRIDGES


@pytest.mark.parametrize("cartridge", PRIORITY_CARTRIDGES)
def test_entities_yaml_loads(cartridge: str) -> None:
    path = CARTRIDGES_ROOT / cartridge / "app" / "config" / "entities.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert isinstance(data.get("entities"), list), \
        f"{cartridge}: entities.yaml.entities must be a list"
    assert len(data["entities"]) >= 5, \
        f"{cartridge}: entities.yaml has too few entries — looks like a stub"
    for item in data["entities"]:
        assert "entity" in item, \
            f"{cartridge}: entity entry must use the 'entity' key (found {item.keys()})"


@pytest.mark.parametrize("cartridge", PRIORITY_CARTRIDGES)
def test_connector_yaml_loads(cartridge: str) -> None:
    path = CARTRIDGES_ROOT / cartridge / "app" / "config" / "connector.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict) and "connector" in data
    conn = data["connector"]
    assert conn["id"] == cartridge
    assert conn["auth"]["type"] in ("basic", "oauth2_client_credentials"), \
        f"{cartridge}: unknown auth type {conn['auth']['type']!r}"


@pytest.mark.parametrize("cartridge", PRIORITY_CARTRIDGES)
def test_knowledge_bits_yaml_loads(cartridge: str) -> None:
    path = CARTRIDGES_ROOT / cartridge / "app" / "config" / "knowledge_bits.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert isinstance(data.get("knowledge_bits"), list)
