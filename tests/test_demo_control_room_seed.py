"""Beta-8 — guards for the Control Room demo/dev seed.

The seed populates the DB-backed Control Room panels (lessons + thresholds)
for the local dev workspace so a demo never shows them empty. Items/alerts
are dataset-driven and out of scope for this seed (documented in the audit).

These are STATIC guards — the SQL is not executed here (no live Postgres);
that path is covered by the stack/CI. We assert the seed is idempotent,
workspace/tenant-scoped, demo-tagged, and production-gated.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SEED = REPO / "infra/init_dev/16_demo_control_room_seed.sql"
COMPOSE = REPO / "infra/docker-compose.yml"


def test_seed_file_exists():
    assert SEED.exists(), "infra/init_dev/16_demo_control_room_seed.sql missing"


def test_seed_is_workspace_and_tenant_scoped():
    src = SEED.read_text(encoding="utf-8")
    assert "Main Workspace" in src and "Default Tenant" in src, (
        "seed must resolve the dev workspace/tenant by name"
    )
    assert "v_workspace_id" in src and "v_tenant_id" in src
    # Every insert must carry workspace_id (no global/unscoped rows).
    assert src.count("workspace_id") >= 3


def test_seed_is_idempotent():
    src = SEED.read_text(encoding="utf-8")
    assert "ON CONFLICT" in src, "thresholds insert must be ON CONFLICT (idempotent)"
    assert re.search(r"WHERE NOT EXISTS", src, re.IGNORECASE), (
        "lessons insert must guard with WHERE NOT EXISTS (no unique index to "
        "ON CONFLICT on)"
    )


def test_seed_rows_are_demo_tagged():
    src = SEED.read_text(encoding="utf-8")
    assert '"demo": true' in src, "seed rows must be tagged metadata.demo=true"


def test_seed_only_touches_db_backed_panels_not_items():
    """Items are dataset-driven; seeding control_room_items would be dead
    data (never shown). The seed must touch only lessons + thresholds."""
    src = SEED.read_text(encoding="utf-8")
    assert "control_room_thresholds" in src
    assert "control_room_lessons" in src
    assert "INSERT INTO control_room_items" not in src, (
        "do not seed control_room_items — dashboard items come from datasets, "
        "so seeded item rows would never appear (dead data)"
    )


def test_compose_dev_seed_runs_the_seed_and_gates_production():
    src = COMPOSE.read_text(encoding="utf-8")
    assert "16_demo_control_room_seed.sql" in src, (
        "dev-seed one-shot must run the demo seed"
    )
    # Production gate: the seed step must be skipped when APP_ENV is prod.
    assert re.search(r"APP_ENV.*=.*production", src), (
        "dev-seed step must skip the demo seed when APP_ENV is production"
    )
