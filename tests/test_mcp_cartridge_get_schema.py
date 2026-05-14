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
    body = json.dumps({"tool": tool, "args": args}).encode("utf-8")
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
    status, body = _invoke(
        "cartridge_get_schema",
        {"cartridge_id": "sap_hcm", "entity": "PA0000Set"},
    )

    assert status == 200
    assert "error" not in body, body
    result = body.get("result", body)
    assert result.get("entity") == "PA0000Set"
