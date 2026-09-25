"""Sprint v1.31 — SAP catalog seed must populate entity_config for real."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
SAP_CARTRIDGES = ("sap_hcm", "sap_s4hana", "sap_successfactors", "sap_b1")
REQUIRED_COLUMNS = (
    "watermark_format",
    "page_size",
    "select_fields",
    "protection",
    "effective_dated",
    "date_field",
    "future_window_days",
)


def _expected_entity_count(cartridge: str) -> int:
    path = REPO_ROOT / "cartridges" / cartridge / "app" / "config" / "entities.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return len(data.get("entities") or [])


def _postgres_dsn() -> str:
    env_dsn = os.environ.get("TEST_POSTGRES_DSN") or os.environ.get("PG_DSN")
    if env_dsn:
        return env_dsn
    env_file = REPO_ROOT / "infra" / ".env"
    values: dict[str, str] = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key] = value
    password = values.get("POSTGRES_PASSWORD", "postgres")
    return (
        f"host=localhost port=15432 dbname=modecissions "
        f"user=postgres password={password}"
    )


def test_entity_config_schema_contains_sap_columns():
    schema = (REPO_ROOT / "infra" / "init" / "00_schema.sql").read_text(encoding="utf-8")
    migration = (REPO_ROOT / "infra" / "init" / "31_entity_config_sap_columns.sql").read_text(encoding="utf-8")
    combined = f"{schema}\n{migration}"

    for column in REQUIRED_COLUMNS:
        assert column in combined


def test_sap_catalog_services_do_not_swallow_seed_errors():
    for cartridge in SAP_CARTRIDGES:
        source = (
            REPO_ROOT / "cartridges" / cartridge / "app" / "services" / "catalog_service.py"
        ).read_text(encoding="utf-8")
        assert "pass  # DB unavailable" not in source
        # A failed seed is logged and re-raised, never swallowed. Two spellings
        # are accepted on purpose: `logger.exception` (with traceback) and, for
        # cartridges hardened against driver exceptions that can embed a DSN or
        # signed request details (sap_successfactors since #640), a
        # `logger.error` that carries the exception class only.
        logs_with_traceback = "logger.exception" in source
        logs_class_only = "logger.error(" in source and "type(exc).__name__" in source
        assert logs_with_traceback or logs_class_only, f"{cartridge}: seed failure is not logged"
        assert "raise" in source
        assert 'e.get("select_fields")' in source
        assert 'e.get("select")' not in source


def test_live_sap_entities_seeded_in_entity_config():
    psycopg2 = pytest.importorskip("psycopg2")
    try:
        conn = psycopg2.connect(_postgres_dsn(), connect_timeout=2)
    except Exception as exc:
        pytest.skip(f"Postgres stack not reachable for live SAP seed check: {exc}")

    with conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT cartridge_id, COUNT(*)
            FROM entity_config
            WHERE cartridge_id IN ('sap_hcm', 'sap_s4hana', 'sap_successfactors', 'sap_b1')
            GROUP BY cartridge_id
            """
        )
        counts = dict(cur.fetchall())
    conn.close()

    for cartridge in SAP_CARTRIDGES:
        expected = _expected_entity_count(cartridge)
        assert counts.get(cartridge, 0) >= expected, (
            f"{cartridge} should have at least {expected} seeded entities, "
            f"got {counts.get(cartridge, 0)}"
        )
