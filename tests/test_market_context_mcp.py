from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib
import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException


ROOT = Path(__file__).resolve().parents[1]
SIGNING_KEY = "market_context_signing_key_64_chars_aaaaaaaaaaaaaaaaaa"
LEGACY_KEY = "market_context_legacy_transport_key_64_chars_bbbbbbbbb"
SERVICE_MARKERS = ("/cartridges/", "/console", "/mcp-infra", "/refinement", "/vault", "/workspace")


def _purge_app_modules() -> None:
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


@pytest.fixture(autouse=True)
def _clean_imports():
    saved = list(sys.path)
    yield
    sys.path[:] = saved
    _purge_app_modules()


def _load_mcp_module(monkeypatch, module: str):
    _purge_app_modules()
    sys.path[:] = [p for p in sys.path if not any(marker in p for marker in SERVICE_MARKERS)]
    sys.path.insert(0, str(ROOT / "mcp-infra"))
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("INTERNAL_API_KEY", LEGACY_KEY)
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("INTERNAL_API_KEY_CONSOLE_TO_MCP_INFRA", "console_to_mcp_key_64_chars_cccccccccccccc")
    monkeypatch.setenv("INTERNAL_API_KEY_MCP_INFRA_TO_REFINEMENT", "mcp_to_refinement_key_64_chars_dddddddddddd")
    monkeypatch.setenv("AIRFLOW_USER", "airflow")
    monkeypatch.setenv("AIRFLOW_PASSWORD", "airflow")
    monkeypatch.setenv("PG_PASSWORD", "postgres")
    monkeypatch.setenv("SUPERSET_USER", "admin")
    monkeypatch.setenv("SUPERSET_PASSWORD", "admin")
    monkeypatch.setenv("MINIO_SECRET_KEY", "miniosecret")
    return importlib.import_module(module)


def _signed(ctx: dict[str, Any]) -> dict[str, Any]:
    signed = {
        **ctx,
        "_signed_at": int(time.time()),
        "_signature_version": "hmac-sha256-v1",
    }
    payload = {name: value for name, value in signed.items() if name != "_signature"}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    signed["_signature"] = hmac.new(SIGNING_KEY.encode(), raw, hashlib.sha256).hexdigest()
    return signed


def _ctx(allowed: list[str]) -> dict[str, Any]:
    return _signed({
        "trusted": True,
        "source": "console",
        "role": "admin",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "workspace_id": "22222222-2222-2222-2222-222222222222",
        "permissions": ["datasets.read"],
        "allowed_cartridges": allowed,
    })


def test_market_context_tool_normalizes_and_filters(monkeypatch):
    mod = _load_mcp_module(monkeypatch, "app.tools.market_context")

    async def fake_query(dataset: str, security_context: dict, limit: int):
        assert security_context["trusted"] is True
        rows = {
            "banxico_market_context": [
                {
                    "series_id": "SF43718",
                    "metric_name": "exchange_rate_usd_mxn",
                    "as_of": "2026-07-10",
                    "value": "18.10",
                    "unit": "MXN_per_USD",
                    "country_code": "MX",
                    "confidence": "0.95",
                    "freshness_status": "ready",
                    "usable": True,
                    "source_authority": "Banxico",
                    "source_host": "www.banxico.org.mx",
                    "source_url": "https://www.banxico.org.mx/secret-free",
                    "payload_hash": "payload",
                    "request_hash": "request",
                    "run_id": "run-1",
                }
            ],
            "sec_market_context": [
                {"ticker": "KO", "metric_name": "Revenue", "usable": False}
            ],
        }
        return rows.get(dataset, []), None

    monkeypatch.setattr(mod, "_query_refinement_dataset", fake_query)
    result = asyncio.run(mod.market_context_read(
        metric_names=["exchange_rate_usd_mxn"],
        usable_only=True,
        security_context=_ctx(["banxico", "sec_edgar"]),
        allowed_providers=["banxico", "sec_edgar"],
    ))

    assert result["count"] == 1
    row = result["context"][0]
    assert row["provider"] == "banxico"
    assert row["identity"] == {"series_id": "SF43718"}
    assert row["value_decimal"] == "18.10"
    assert row["evidence"]["source_host"] == "www.banxico.org.mx"
    assert "source_url" not in json.dumps(row)


def test_market_context_prod_requires_pair_key(monkeypatch):
    mod = _load_mcp_module(monkeypatch, "app.tools.market_context")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("INTERNAL_API_KEY_MCP_INFRA_TO_REFINEMENT", raising=False)

    with pytest.raises(HTTPException) as exc:
        mod._refinement_headers()

    assert exc.value.status_code == 500


def test_market_context_scope_injected_from_trusted_context(monkeypatch):
    main = _load_mcp_module(monkeypatch, "app.main")
    req = main.InvokeRequest(tool="market_context_read", args={}, security_context=_ctx(["banxico"]))

    main._enforce_data_scope(req, "console")

    assert req.args["allowed_providers"] == ["banxico"]
    assert req.args["security_context"]["tenant_id"] == "11111111-1111-1111-1111-111111111111"


def test_market_context_rejects_unallowed_requested_provider(monkeypatch):
    main = _load_mcp_module(monkeypatch, "app.main")
    req = main.InvokeRequest(
        tool="market_context_read",
        args={"provider": "sec_edgar"},
        security_context=_ctx(["banxico"]),
    )

    with pytest.raises(HTTPException) as exc:
        main._enforce_data_scope(req, "console")

    assert exc.value.status_code == 403
