import importlib
import sys
from pathlib import Path

from fastapi.testclient import TestClient


def _load_vault_main(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    monkeypatch.setenv("INTERNAL_API_KEY", "test_internal_key_with_more_than_32_chars")
    sys.modules.pop("app.main", None)
    sys.modules.pop("app.security", None)
    return importlib.import_module("app.main")


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
    monkeypatch.setattr(main, "_db_list", lambda scope, cartridge: [])

    client = TestClient(main.app)
    response = client.get(
        "/connections/replicon",
        headers={
            "x-api-key": "test_internal_key_with_more_than_32_chars",
            "x-internal-service": "console",
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
        lambda scope, cartridge: [
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
