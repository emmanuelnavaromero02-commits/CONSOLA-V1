from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import sys
import time
from pathlib import Path

import pytest
from fastapi import HTTPException


REPO_ROOT = Path(__file__).resolve().parents[1]
SIGNING_KEY = "security_context_signing_key_distinct_64_chars_aaaaaaaa"
LEGACY_KEY = "legacy_internal_transport_key_64_chars_bbbbbbbbbbbbbbbb"
SERVICE_PATH_MARKERS = (
    "/cartridges/",
    "/console",
    "/mcp-infra",
    "/refinement",
    "/vault",
    "/workspace",
)


def _purge_app_modules() -> None:
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


def _load_service(service: str, monkeypatch):
    _purge_app_modules()
    sys.path[:] = [
        p for p in sys.path if not any(marker in p for marker in SERVICE_PATH_MARKERS)
    ]
    sys.path.insert(0, str(REPO_ROOT / service))
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("INTERNAL_API_KEY", LEGACY_KEY)
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv(
        "INTERNAL_API_KEY_CONSOLE_TO_MCP_INFRA",
        "console_to_mcp_transport_key_64_chars_cccccccccc",
    )
    monkeypatch.setenv(
        "INTERNAL_API_KEY_CONSOLE_TO_VAULT",
        "console_to_vault_transport_key_64_chars_dddddddddd",
    )
    monkeypatch.setenv(
        "INTERNAL_API_KEY_CONSOLE_TO_REFINEMENT",
        "console_to_refinement_transport_key_64_chars_eeee",
    )
    monkeypatch.setenv(
        "INTERNAL_API_KEY_MCP_INFRA_TO_REFINEMENT",
        "mcp_to_refinement_transport_key_64_chars_fffff",
    )
    monkeypatch.setenv(
        "VAULT_ENCRYPTION_KEY", "8sXi-0kBYU5DJ5dY7CCRkW7XHJsXxLPmO6r9OYx-3a4="
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
    monkeypatch.setenv("AIRFLOW_USER", "airflow")
    monkeypatch.setenv("AIRFLOW_PASSWORD", "airflow")
    monkeypatch.setenv("PG_PASSWORD", "postgres")
    monkeypatch.setenv("SUPERSET_USER", "admin")
    monkeypatch.setenv("SUPERSET_PASSWORD", "admin")
    monkeypatch.setenv("MINIO_SECRET_KEY", "miniosecret")
    return importlib.import_module("app.main")


def _signed(ctx: dict, *, signed_at: int | None = None, key: str = SIGNING_KEY) -> dict:
    signed = {
        **ctx,
        "_signed_at": int(time.time()) if signed_at is None else signed_at,
        "_signature_version": "hmac-sha256-v1",
    }
    payload = {name: value for name, value in signed.items() if name != "_signature"}
    raw = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    signed["_signature"] = hmac.new(
        key.encode("utf-8"), raw, hashlib.sha256
    ).hexdigest()
    return signed


def _trusted_ctx() -> dict:
    return {
        "trusted": True,
        "source": "console",
        "role": "admin",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "workspace_id": "22222222-2222-2222-2222-222222222222",
        "permissions": ["datasets.read"],
        "allowed_cartridges": ["hubspot"],
    }


def test_mcp_rejects_expired_trusted_context_even_in_test(monkeypatch):
    main = _load_service("mcp-infra", monkeypatch)
    ctx = _signed(_trusted_ctx(), signed_at=int(time.time()) - 301)
    req = main.InvokeRequest(tool="postgres_list_tables", args={}, security_context=ctx)

    with pytest.raises(HTTPException) as exc:
        main._ctx(req, "console")

    assert exc.value.status_code == 403


def test_refinement_rejects_signed_at_tampering_even_in_test(monkeypatch):
    main = _load_service("refinement", monkeypatch)
    ctx = _signed(_trusted_ctx())
    ctx["_signed_at"] = int(ctx["_signed_at"]) + 1

    with pytest.raises(HTTPException) as exc:
        main._security_context(
            {"security_context": ctx, "_verified_internal_service": "console"}
        )

    assert exc.value.status_code == 403


def test_refinement_accepts_console_context_forwarded_by_mcp_infra(monkeypatch):
    main = _load_service("refinement", monkeypatch)
    ctx = _signed(_trusted_ctx())

    resolved = main._security_context(
        {
            "security_context": ctx,
            "_verified_internal_service": "mcp-infra",
        }
    )

    assert resolved["source"] == "console"
    assert resolved["tenant_id"] == _trusted_ctx()["tenant_id"]


def test_refinement_rejects_legacy_airflow_context(monkeypatch):
    main = _load_service("refinement", monkeypatch)
    ctx = _signed(
        {
            "trusted": True,
            "source": "airflow",
            "audience": "refinement",
            "purpose": "refinement.mcp.materialize",
            "tool": "materialize",
            "dataset": "pnl_mensual",
            "user_id": "airflow:dataset_refresh_chain",
            "role": "admin",
            "workspace_role": "service",
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "workspace_id": "22222222-2222-2222-2222-222222222222",
            "permissions": ["datasets.read", "datasets.write"],
            "allowed_cartridges": ["replicon"],
            "allowed_prefixes": [
                "raw/replicon/",
                "silver/replicon/tenant_id=11111111-1111-1111-1111-111111111111/"
                "workspace_id=22222222-2222-2222-2222-222222222222/",
                "gold/replicon/tenant_id=11111111-1111-1111-1111-111111111111/"
                "workspace_id=22222222-2222-2222-2222-222222222222/",
                "uploads/replicon/tenant_id=11111111-1111-1111-1111-111111111111/"
                "workspace_id=22222222-2222-2222-2222-222222222222/",
                "cartridges/replicon/",
            ],
        }
    )
    body = {
        "tool": "materialize",
        "args": {"name": "pnl_mensual"},
        "security_context": ctx,
        "_verified_internal_service": "airflow",
    }

    with pytest.raises(HTTPException) as exc:
        main._security_context(body)
    assert exc.value.status_code == 403


def test_refinement_airflow_v2_is_bound_and_consumed(monkeypatch):
    main = _load_service("refinement", monkeypatch)
    runtime = importlib.import_module("app.runtime_security_context")
    consumed: list[str] = []
    monkeypatch.setattr(
        runtime,
        "_consume_runtime_jti",
        lambda context, _signed_at: consumed.append(str(context["jti"])),
    )
    from airflow.dags.runtime_security_context import build_materialize_context

    context = build_materialize_context(
        tenant_id="11111111-1111-1111-1111-111111111111",
        workspace_id="22222222-2222-2222-2222-222222222222",
        cartridge_id="replicon",
        dataset_name="pnl_mensual",
        run_id="scheduled__2026-08-01T00:00:00Z",
    )
    body = {
        "tool": "materialize",
        "args": {"name": "pnl_mensual"},
        "security_context": context,
        "_verified_internal_service": "airflow",
    }

    assert main._security_context(body)["dataset"] == "pnl_mensual"
    assert consumed == [context["jti"]]

    tampered = build_materialize_context(
        tenant_id="11111111-1111-1111-1111-111111111111",
        workspace_id="22222222-2222-2222-2222-222222222222",
        cartridge_id="replicon",
        dataset_name="pnl_mensual",
        run_id="scheduled__2026-08-01T00:00:01Z",
    )
    with pytest.raises(HTTPException) as exc:
        main._security_context(
            {
                "tool": "materialize",
                "args": {"name": "another_dataset"},
                "security_context": tampered,
                "_verified_internal_service": "airflow",
            }
        )
    assert exc.value.status_code == 403


def test_vault_rejects_missing_security_context_signing_key_even_in_test(monkeypatch):
    main = _load_service("vault", monkeypatch)
    ctx = _signed(_trusted_ctx())
    monkeypatch.delenv("SECURITY_CONTEXT_SIGNING_KEY", raising=False)

    with pytest.raises(HTTPException) as exc:
        main._security_context_from_header(json.dumps(ctx))

    assert exc.value.status_code == 403


def test_vault_rejects_security_context_signing_key_reused_as_transport_key(
    monkeypatch,
):
    main = _load_service("vault", monkeypatch)
    ctx = _signed(_trusted_ctx(), key=LEGACY_KEY)
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", LEGACY_KEY)

    with pytest.raises(HTTPException) as exc:
        main._security_context_from_header(json.dumps(ctx))

    assert exc.value.status_code == 403


@pytest.fixture(autouse=True)
def _restore_imports():
    yield
    _purge_app_modules()
