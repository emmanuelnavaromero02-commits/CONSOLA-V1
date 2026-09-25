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
    assert re.search(r"APP_ENV.*=.*production", src), (
        "dev-seed step must skip the demo seed when APP_ENV is production"
    )


def test_compose_local_bootstrap_seed_is_inside_non_production_branch():
    src = COMPOSE.read_text(encoding="utf-8")
    command_start = src.index("postgres_dev_seed:")
    command_end = src.index("restart:", command_start)
    command = src[command_start:command_end]

    assert "15_local_dev_bootstrap.sql" in command
    prod_gate = command.index("APP_ENV")
    first_bootstrap_run = command.index("15_local_dev_bootstrap.sql")
    else_branch = command.index("else")

    assert else_branch < first_bootstrap_run
    assert prod_gate < first_bootstrap_run
