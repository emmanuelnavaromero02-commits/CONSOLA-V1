from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MIGRATION = REPO / "infra/init/42_cartridges_in_mcp_servers.sql"
BACKFILL = REPO / "infra/init/96_salesforce_mcp_server_registry.sql"


def _src() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_migration_exists():
    assert MIGRATION.exists(), f"missing {MIGRATION}"
    assert BACKFILL.exists(), f"missing {BACKFILL}"


def test_migration_42_adds_builtin_cartridges():
    src = _src()
    for cart_id in (
        "hubspot",
        "replicon",
        "salesforce",
        "sap_hcm",
        "sap_successfactors",
        "sap_s4hana",
    ):
        assert f"'{cart_id}'" in src, (
            f"migration 42 missing INSERT for cartridge {cart_id!r}"
        )


def test_migration_42_uses_cartridge_category():
    src = _src()
    assert src.count("'cartridge'") >= 6


def test_migration_42_idempotent():
    src = _src()
    assert "ON CONFLICT (id) DO UPDATE" in src
    set_clause_match = re.search(
        r"ON CONFLICT \(id\) DO UPDATE SET\b(.+?);",
        src, re.DOTALL,
    )
    assert set_clause_match, "DO UPDATE SET clause not found"
    set_body = set_clause_match.group(1)
    assert "tools" not in set_body, (
        "DO UPDATE SET must not overwrite the tools column"
    )
    assert "healthy" not in set_body, (
        "DO UPDATE SET must not overwrite the healthy column"
    )


def test_migration_42_ordering():
    init = REPO / "infra/init"
    names = sorted(p.name for p in init.glob("*.sql"))
    assert names.index("00_schema.sql") < names.index("42_cartridges_in_mcp_servers.sql")
    assert names.index("41_copilot_query_indexes.sql") < names.index("42_cartridges_in_mcp_servers.sql")
    assert names.index("42_cartridges_in_mcp_servers.sql") < names.index("96_salesforce_mcp_server_registry.sql")


def test_salesforce_backfill_migration_idempotent_and_preserves_health_state():
    src = BACKFILL.read_text(encoding="utf-8")
    assert "'salesforce'" in src
    assert "'http://salesforce:8205'" in src
    assert "'cartridge'" in src
    assert "ON CONFLICT (id) DO UPDATE" in src
    set_clause_match = re.search(
        r"ON CONFLICT \(id\) DO UPDATE SET\b(.+?);",
        src,
        re.DOTALL,
    )
    assert set_clause_match, "DO UPDATE SET clause not found"
    set_body = set_clause_match.group(1)
    assert "tools" not in set_body
    assert "healthy" not in set_body
    assert "last_seen" not in set_body


def test_mcp_registry_startup_includes_builtin_cartridges():
    src = (REPO / "console/app/services/mcp_registry.py").read_text(encoding="utf-8")
    m = re.search(r"async def startup\(\).*?\n    for server in builtin:", src, re.DOTALL)
    assert m, "startup() function not found"
    body = m.group(0)
    for cart_id in (
        "hubspot",
        "replicon",
        "salesforce",
        "sap_hcm",
        "sap_successfactors",
        "sap_s4hana",
        "sap_b1",
    ):
        assert f'"id":          "{cart_id}"' in body, (
            f"startup() missing cartridge {cart_id!r}"
        )
    assert body.count('"category":    "cartridge"') == 7


def test_mcp_registry_startup_cartridge_urls_from_env():
    src = (REPO / "console/app/services/mcp_registry.py").read_text(encoding="utf-8")
    for env_var, default in [
        ("HUBSPOT_URL",           "http://hubspot:8210"),
        ("REPLICON_URL",           "http://replicon:8201"),
        ("SALESFORCE_URL",         "http://salesforce:8205"),
        ("SAP_HCM_URL",            "http://sap-hcm:8202"),
        ("SAP_SUCCESSFACTORS_URL", "http://sap-successfactors:8203"),
        ("SAP_S4HANA_URL",         "http://sap-s4hana:8204"),
        ("SAP_B1_URL",             "http://sap-b1:8206"),
    ]:
        assert f'os.environ.get("{env_var}", "{default}")' in src, (
            f"{env_var} not used with default {default!r}"
        )


def test_mcp_registry_console_tools_use_internal_url():
    src = (REPO / "console/app/services/mcp_registry.py").read_text(encoding="utf-8")
    assert 'os.environ.get("CONSOLE_INTERNAL_URL", "http://console:8000")' in src
    assert 'os.environ.get("CONSOLE_URL", "http://console:8000")' not in src
