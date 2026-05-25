"""Sprint v1.31 — mcp-infra cartridge_get_schema must not query dead columns."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
MCP_INFRA_BASE = os.environ.get("OMEGA_MCP_INFRA_BASE", "http://localhost:8010")


def _internal_key() -> str:
    env_file = REPO_ROOT / "infra" / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("INTERNAL_API_KEY_CONSOLE_TO_MCP_INFRA="):
                return line.split("=", 1)[1]
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("INTERNAL_API_KEY="):
                return line.split("=", 1)[1]
    env_key = (
        os.environ.get("INTERNAL_API_KEY_CONSOLE_TO_MCP_INFRA")
        or os.environ.get("INTERNAL_API_KEY")
    )
    if env_key:
        return env_key
    return "test-internal-key-aaaaaaaaaaaaaaaaaaaaaaaa"


def _invoke(tool: str, args: dict) -> tuple[int, dict]:
    body = json.dumps({
        "tool": tool,
        "args": args,
        "security_context": {
            "trusted": True,
            "source": "console",
            "role": "admin",
            "permissions": ["cartridges.read"],
            "allowed_prefixes": ["raw/", "silver/", "gold/", "cartridges/"],
        },
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{MCP_INFRA_BASE}/mcp/invoke",
        method="POST",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Api-Key": _internal_key(),
            "X-Internal-Service": "console",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read())
        except Exception:
            payload = {"error": str(exc)}
        return exc.code, payload
    except Exception as exc:
        pytest.skip(f"mcp-infra not reachable at {MCP_INFRA_BASE}: {exc}")


def test_cartridge_get_schema_with_replicon_returns_200():
    status, body = _invoke(
        "cartridge_get_schema",
        {"cartridge_id": "replicon", "entity": "User"},
    )

    assert status == 200
    assert "error" not in body, body
    result = body.get("result", body)
    assert result.get("entity") == "User"


def test_cartridge_get_schema_with_sap_hcm_returns_200():
    # PA0000Set is the technical OData entityset; after the Block-A re-alignment
    # entity_config stores the business name (EmployeeActions). The triple lookup
    # resolves the technical last-segment to the business entity.
    status, body = _invoke(
        "cartridge_get_schema",
        {"cartridge_id": "sap_hcm", "entity": "PA0000Set"},
    )

    assert status == 200
    assert "error" not in body, body
    result = body.get("result", body)
    assert result.get("entity") == "EmployeeActions"
    assert str(result.get("odata_entity") or "").endswith("PA0000Set")


@pytest.mark.parametrize(
    "requested",
    ["EmployeeActions", "HRPA_EE_PA_SRV/PA0000Set", "PA0000Set"],
    ids=["business_name", "full_odata_entity", "odata_last_segment"],
)
def test_cartridge_get_schema_accepts_three_lookup_forms(requested):
    """The same HCM entity is reachable by business name, full odata_entity, or
    the last segment of the odata_entity — all resolve to EmployeeActions."""
    status, body = _invoke(
        "cartridge_get_schema",
        {"cartridge_id": "sap_hcm", "entity": requested},
    )
    assert status == 200
    assert "error" not in body, body
    result = body.get("result", body)
    assert result.get("entity") == "EmployeeActions"


def test_cartridge_get_schema_unknown_entity_still_not_found():
    status, body = _invoke(
        "cartridge_get_schema",
        {"cartridge_id": "sap_hcm", "entity": "DefinitelyNotAnEntity"},
    )
    result = body.get("result", body)
    assert "error" in result, result
    assert "not found" in result["error"].lower()


def _load_pure_matcher():
    """Load _entity_name_matches from the source without importing the heavy
    cartridges module (which pulls pandas/duckdb, absent in unit envs)."""
    import ast

    src = (REPO_ROOT / "mcp-infra" / "app" / "tools" / "cartridges.py").read_text(encoding="utf-8")
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == "_entity_name_matches":
            ns: dict = {}
            exec(ast.get_source_segment(src, node), ns)
            return ns["_entity_name_matches"]
    raise AssertionError("_entity_name_matches not found in cartridges.py")


def test_entity_name_matching_logic():
    """Pure-logic coverage of the triple lookup (runs without a live stack)."""
    m = _load_pure_matcher()
    odata = "HRPA_EE_PA_SRV/PA0000Set"
    assert m("EmployeeActions", "EmployeeActions", odata) is True   # business name
    assert m(odata, "EmployeeActions", odata) is True               # full odata_entity
    assert m("PA0000Set", "EmployeeActions", odata) is True         # last segment
    assert m("Nope", "EmployeeActions", odata) is False             # invalid -> rejected
    assert m("User", "User", None) is True                          # no odata_entity
    assert m("PA0000Set", "BusinessPartner", None) is False         # technical req, no odata
    # underscore in the technical name must match literally (no LIKE wildcards)
    assert m("A_BusinessPartner", "BusinessPartner", "API_BUSINESS_PARTNER/A_BusinessPartner") is True
