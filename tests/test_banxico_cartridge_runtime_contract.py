from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]


def _yaml(path: str) -> dict:
    return yaml.safe_load((REPO / path).read_text(encoding="utf-8"))


def test_banxico_local_compose_service_is_bronze_only():
    svc = _yaml("infra/docker-compose.yml")["services"]["banxico"]
    assert svc["build"]["context"] == ".."
    assert svc["build"]["dockerfile"] == "cartridges/banxico/Dockerfile"
    assert svc["container_name"] == "mode_banxico"
    assert "8215:8215" in svc["ports"]
    assert svc["environment"]["BANXICO_API_TOKEN"] == "${BANXICO_API_TOKEN:-}"
    assert svc["environment"]["INTERNAL_API_KEY_BANXICO_TO_CONSOLE"] == "${INTERNAL_API_KEY_BANXICO_TO_CONSOLE:-}"
    assert svc["environment"]["CONSOLE_URL"] == "http://console:8000"
    assert "omega_cartridge_banxico" in svc["environment"]["DATABASE_URL"]


def test_banxico_seed_has_no_downstream_engines():
    src = (REPO / "infra/init/95_banxico_role_and_seed.sql").read_text(encoding="utf-8")
    assert "omega_cartridge_banxico" in src
    assert "banxico_extract" in src
    for forbidden in ("mcp_servers", "silver", "gold", "dataset_refresh_chain", "control_room"):
        assert forbidden not in src.lower()


def test_banxico_dag_is_mounted_for_airflow_runtime():
    compose = (REPO / "infra/docker-compose.yml").read_text(encoding="utf-8")
    assert "BANXICO_URL" in compose
    assert "cartridges/banxico/dags" in compose
