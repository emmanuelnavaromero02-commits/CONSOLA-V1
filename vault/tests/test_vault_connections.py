import importlib
import hashlib
import hmac
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient


def _load_vault_main(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    sys.path[:] = [p for p in sys.path if p != str(root)]
    sys.path.insert(0, str(root))
    monkeypatch.setenv("INTERNAL_API_KEY", "test_internal_key_with_more_than_32_chars")
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", "s" * 64)
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    return importlib.import_module("app.main")


def _security_context_header() -> str:
    ctx = {
        "trusted": True,
        "source": "console",
        "role": "admin",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "workspace_id": "22222222-2222-2222-2222-222222222222",
        "allowed_cartridges": ["replicon"],
        "_signed_at": 1,
        "_signature_version": "hmac-sha256-v1",
    }
    payload = {key: value for key, value in ctx.items() if key not in {"_signature", "_signed_at", "_signature_version"}}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ctx["_signature"] = hmac.new(("s" * 64).encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return json.dumps(ctx)


def test_normalize_postgres_dsn_accepts_sqlalchemy_psycopg2_urls(monkeypatch):
    main = _load_vault_main(monkeypatch)

    assert (
        main._normalize_postgres_dsn("postgresql+psycopg2://u:p@h/db")
        == "postgresql://u:p@h/db"
    )
    assert (
        main._normalize_postgres_dsn("postgres+psycopg2://u:p@h/db")
        == "postgres://u:p@h/db"
    )
    assert (
        main._normalize_postgres_dsn("postgresql://u:p@h/db")
        == "postgresql://u:p@h/db"
    )


def test_list_connections_empty_state_returns_connections_array(monkeypatch):
    main = _load_vault_main(monkeypatch)
    monkeypatch.setattr(main, "_seed", lambda: None)
    monkeypatch.setattr(main, "_db_list", lambda scope, cartridge, ctx=None: [])

    client = TestClient(main.app)
    response = client.get(
        "/connections/replicon",
        headers={
            "x-api-key": "test_internal_key_with_more_than_32_chars",
            "x-internal-service": "console",
            "x-security-context": _security_context_header(),
        },
    )

    assert response.status_code == 200
    assert response.json() == {"connections": []}


def test_list_connections_masks_secret_fields(monkeypatch):
    main = _load_vault_main(monkeypatch)
    monkeypatch.setattr(main, "_seed", lambda: None)
    monkeypatch.setattr(
        main,
        "_db_list",
        lambda scope, cartridge, ctx=None: [
            {
                "key": "analytics",
                "value": {
                    "base_url": "https://example.test",
                    "token": "real-token",
                    "password": "real-password",
                    "api_key": "real-api-key",
                },
            }
        ],
    )

    client = TestClient(main.app)
    response = client.get(
        "/connections/replicon",
        headers={
            "x-api-key": "test_internal_key_with_more_than_32_chars",
            "x-internal-service": "console",
            "x-security-context": _security_context_header(),
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "connections": [
            {
                "conn_id": "analytics",
                "base_url": "https://example.test",
                "token": "***",
                "password": "***",
                "api_key": "***",
            }
        ]
    }
