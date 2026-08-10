"""P11 — config contract for every cartridge's packaged YAML.

sap_successfactors and replicon had no dedicated unit tests of their own
config (the SAP suites are mostly parametrized contract tests). These pure
checks pin the minimal shape of each cartridge's entities / knowledge_bits /
connector YAML for all built-in cartridges, so a malformed config is caught without
a live SAP/MinIO backend.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CARTRIDGES = ("sap_hcm", "sap_s4hana", "sap_successfactors", "replicon", "hubspot")


def _config(cartridge: str, name: str) -> dict:
    path = REPO_ROOT / "cartridges" / cartridge / "app" / "config" / name
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


@pytest.mark.parametrize("cartridge", CARTRIDGES)
def test_entities_yaml_loads_with_minimal_fields(cartridge):
    entities = _config(cartridge, "entities.yaml").get("entities") or []
    assert entities, f"{cartridge}: entities.yaml has no entities"
    for entry in entities:
        assert (
            isinstance(entry.get("entity"), str) and entry["entity"].strip()
        ), f"{cartridge}: an entity entry is missing a non-empty 'entity'"
        assert entry.get(
            "mode"
        ), f"{cartridge}: entity {entry.get('entity')!r} has no 'mode'"


@pytest.mark.parametrize("cartridge", CARTRIDGES)
def test_knowledge_bits_yaml_loads_with_id_and_sql(cartridge):
    kbs = _config(cartridge, "knowledge_bits.yaml").get("knowledge_bits") or []
    assert kbs, f"{cartridge}: knowledge_bits.yaml has no knowledge_bits"
    config_root = (REPO_ROOT / "cartridges" / cartridge / "app" / "config").resolve()
    for kb in kbs:
        assert kb.get("id"), f"{cartridge}: a knowledge bit is missing 'id'"
        inline_sql = kb.get("sql")
        sql_file = kb.get("sql_file")
        has_inline_sql = isinstance(inline_sql, str) and bool(inline_sql.strip())
        has_sql_file = isinstance(sql_file, str) and bool(sql_file.strip())
        assert has_inline_sql != has_sql_file, (
            f"{cartridge}: knowledge bit {kb.get('id')!r} must declare exactly "
            "one of 'sql' or 'sql_file'"
        )
        if has_sql_file:
            sql_path = (config_root / sql_file).resolve()
            assert sql_path.is_relative_to(
                config_root
            ), f"{cartridge}: knowledge bit {kb.get('id')!r} sql_file escapes config"
            assert (
                sql_path.is_file()
            ), f"{cartridge}: knowledge bit {kb.get('id')!r} sql_file is missing"
            assert sql_path.read_text(
                encoding="utf-8"
            ).strip(), f"{cartridge}: knowledge bit {kb.get('id')!r} sql_file is empty"


@pytest.mark.parametrize("cartridge", CARTRIDGES)
def test_connector_yaml_loads_with_id_and_auth_type(cartridge):
    connector = _config(cartridge, "connector.yaml").get("connector") or {}
    assert connector.get("id"), f"{cartridge}: connector.yaml has no connector.id"
    auth = connector.get("auth") or {}
    assert auth.get("type"), f"{cartridge}: connector.yaml has no connector.auth.type"
