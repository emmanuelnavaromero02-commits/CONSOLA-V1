"""Sprint v1.43.1 — Codex P0-3: cartridges registered in mcp_servers.

Static verification of:
  * migration 42 SQL seeds the 4 cartridge rows and is idempotent.
  * mcp_registry.startup() includes the 4 cartridges so the rows stay
    fresh on every console restart (and the tools JSONB gets populated
    via /mcp/tools HTTP fetch).
"""
from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MIGRATION = REPO / "infra/init/42_cartridges_in_mcp_servers.sql"


def _src() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_migration_exists():
    assert MIGRATION.exists(), f"missing {MIGRATION}"


def test_migration_42_adds_4_cartridges():
    src = _src()
    for cart_id in ("replicon", "sap_hcm", "sap_successfactors", "sap_s4hana"):
        assert f"'{cart_id}'" in src, (
            f"migration 42 missing INSERT for cartridge {cart_id!r}"
        )


def test_migration_42_uses_cartridge_category():
    """All 4 must be tagged ``category='cartridge'`` so the tool
    manifest filter (which groups by category) sees them in the
    right bucket."""
    src = _src()
    assert src.count("'cartridge'") >= 4


def test_migration_42_idempotent():
    """Re-running must be a no-op via ON CONFLICT DO UPDATE."""
    src = _src()
    assert "ON CONFLICT (id) DO UPDATE" in src
    # The conflict update must NOT touch tools / healthy / last_seen
    # — those columns are owned by mcp_registry.register() at console
    # boot (HTTP fetch) and shouldn't be overwritten by a re-applied
    # migration. We inspect only the SET clause (between DO UPDATE SET
    # and the trailing semicolon) so the top-of-file comments don't
    # produce false positives.
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
    """41 (copilot indexes) → 42 (cartridges registry). Lexicographic
    ordering ensures docker-entrypoint-initdb.d processes 42 after 41,
    after 00 (the CREATE TABLE for mcp_servers)."""
    init = REPO / "infra/init"
    names = sorted(p.name for p in init.glob("*.sql"))
    assert names.index("00_schema.sql") < names.index("42_cartridges_in_mcp_servers.sql")
    assert names.index("41_copilot_query_indexes.sql") < names.index("42_cartridges_in_mcp_servers.sql")


def test_mcp_registry_startup_includes_4_cartridges():
    """v1.43.1: console boot must HTTP-sync each cartridge's /mcp/tools.
    Verify by reading the source — the builtin list in startup() has
    entries for all 4 cartridges with category='cartridge'."""
    src = (REPO / "console/app/services/mcp_registry.py").read_text(encoding="utf-8")
    # Find the startup() function body.
    m = re.search(r"async def startup\(\).*?\n    for server in builtin:", src, re.DOTALL)
    assert m, "startup() function not found"
    body = m.group(0)
    for cart_id in ("replicon", "sap_hcm", "sap_successfactors", "sap_s4hana"):
        assert f'"id":          "{cart_id}"' in body, (
            f"startup() missing cartridge {cart_id!r}"
        )
    # And every cartridge is in the cartridge category, not the
    # legacy 'mcp' or 'monitoring' buckets.
    assert body.count('"category":    "cartridge"') == 4


def test_mcp_registry_startup_cartridge_urls_from_env():
    """Each cartridge URL is env-var-driven (same convention the SAP
    DAGs now use). Compose defaults match the local service hostnames."""
    src = (REPO / "console/app/services/mcp_registry.py").read_text(encoding="utf-8")
    for env_var, default in [
        ("REPLICON_URL",           "http://replicon:8201"),
        ("SAP_HCM_URL",            "http://sap-hcm:8202"),
        ("SAP_SUCCESSFACTORS_URL", "http://sap-successfactors:8203"),
        ("SAP_S4HANA_URL",         "http://sap-s4hana:8204"),
    ]:
        assert f'os.environ.get("{env_var}", "{default}")' in src, (
            f"{env_var} not used with default {default!r}"
        )
