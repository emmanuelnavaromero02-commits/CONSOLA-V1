"""Sprint v1.41.1 — pins the v1.41.1 observability indexes.

Without these the /api/metrics/operational and /api/freshness queries
seq-scan extraction_runs / audit_events as soon as volume grows.
"""
from __future__ import annotations

from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1] / "infra" / "init"
    / "40_v141_observability_indexes.sql"
)


def _src() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_migration_exists():
    assert MIGRATION.exists(), f"missing {MIGRATION}"


def test_audit_events_created_at_index():
    src = _src()
    assert "idx_audit_events_created_at" in src
    assert "ON audit_events" in src
    assert "created_at" in src


def test_extraction_runs_started_at_index():
    src = _src()
    assert "idx_extraction_runs_started_at" in src
    assert "ON extraction_runs" in src


def test_extraction_runs_cartridge_entity_started_index():
    src = _src()
    assert "idx_extraction_runs_cartridge_entity_started" in src
    assert "cartridge_id" in src
    assert "entity_name" in src


def test_migration_is_idempotent():
    src = _src()
    create_lines = [ln for ln in src.splitlines() if ln.strip().startswith("CREATE INDEX")]
    assert len(create_lines) == 3, create_lines
    for ln in create_lines:
        assert "IF NOT EXISTS" in ln, f"non-idempotent CREATE INDEX: {ln}"


def test_migration_ordering():
    init = Path(__file__).resolve().parents[1] / "infra" / "init"
    names = sorted(p.name for p in init.glob("*.sql"))
    assert names.index("00_schema.sql") < names.index("40_v141_observability_indexes.sql")
    assert names.index("16_audit_events.sql") < names.index("40_v141_observability_indexes.sql")
