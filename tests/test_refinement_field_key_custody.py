from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
LOCAL_COMPOSE = ROOT / "infra/docker-compose.yml"
AWS_COMPOSE = ROOT / "infra/terraform/deploy/docker-compose.aws.yml"
REFINEMENT_APP = ROOT / "refinement/app"
MATERIALIZER = REFINEMENT_APP / "successfactors_exposure_materializer.py"
KEY = "FIELD_ENCRYPTION_KEY"
NEVER_HOLDERS = (
    "console",
    "workspace",
    "vault",
    "mcp-infra",
    "superset",
    "superset-init",
    "postgres",
    "postgres_gold",
    "redis",
)


def _services(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["services"]


@pytest.mark.parametrize("compose", [LOCAL_COMPOSE, AWS_COMPOSE], ids=["local", "aws"])
def test_refinement_receives_the_field_key_as_optional(compose: Path) -> None:
    env = _services(compose)["refinement"]["environment"]
    assert env[KEY] == "${FIELD_ENCRYPTION_KEY:-}"


@pytest.mark.parametrize("compose", [LOCAL_COMPOSE, AWS_COMPOSE], ids=["local", "aws"])
def test_services_without_a_decryption_need_never_declare_the_field_key(
    compose: Path,
) -> None:
    services = _services(compose)
    for name in NEVER_HOLDERS:
        if name in services:
            env = services[name].get("environment") or {}
            assert KEY not in env, f"{name} must not receive {KEY}"


def test_only_the_aggregate_materializer_reads_the_field_key() -> None:
    readers = sorted(
        path.name
        for path in REFINEMENT_APP.rglob("*.py")
        if KEY in path.read_text(encoding="utf-8")
    )
    assert readers == [MATERIALIZER.name]


def test_refinement_never_registers_python_functions_on_duckdb() -> None:
    offenders = sorted(
        path.name
        for path in REFINEMENT_APP.rglob("*.py")
        if re.search(r"\.create_function\s*\(", path.read_text(encoding="utf-8"))
    )
    assert offenders == []


def test_materializer_reads_the_key_at_call_time_only() -> None:
    source = MATERIALIZER.read_text(encoding="utf-8")
    module_level = [
        line
        for line in source.splitlines()
        if line and not line.startswith((" ", "\t", "#")) and "environ" in line
    ]
    assert module_level == []
    assert "exc_info" not in source and "logger.exception" not in source
