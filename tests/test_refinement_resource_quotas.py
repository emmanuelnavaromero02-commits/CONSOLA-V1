from __future__ import annotations

from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[1]
COMPOSE = REPO / "infra/docker-compose.yml"
ENV_EXAMPLE = REPO / "infra/.env.example"


def test_refinement_compose_has_container_resource_quotas():
    doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    svc = doc["services"]["refinement"]

    assert svc["mem_limit"] == "${REFINEMENT_MEM_LIMIT:-1g}"
    assert svc["cpus"] == "${REFINEMENT_CPUS:-1.0}"


def test_refinement_compose_sets_duckdb_runtime_limits():
    doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    env = doc["services"]["refinement"]["environment"]

    assert env["DUCKDB_MEMORY_LIMIT"] == "${DUCKDB_MEMORY_LIMIT:-512MB}"
    assert env["DUCKDB_THREADS"] == "${DUCKDB_THREADS:-2}"


def test_env_example_documents_refinement_duckdb_quotas():
    src = ENV_EXAMPLE.read_text(encoding="utf-8")
    for needle in (
        "REFINEMENT_MEM_LIMIT=1g",
        "REFINEMENT_CPUS=1.0",
        "DUCKDB_MEMORY_LIMIT=512MB",
        "DUCKDB_THREADS=2",
    ):
        assert needle in src
