from __future__ import annotations

import json
import math
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "infra" / "docker-compose.yml"
ENV_EXAMPLE = ROOT / "infra" / ".env.example"
AWS_COMPOSE = ROOT / "infra" / "terraform" / "deploy" / "docker-compose.aws.yml"
AWS_CARTRIDGES_COMPOSE = (
    ROOT / "infra" / "terraform" / "deploy" / "docker-compose.cartridges.yml"
)
AWS_ENV_EXAMPLE = ROOT / "infra" / "terraform" / "deploy" / ".env.example"

HOST_RAM_BYTES = 7 * 1024**3 + 600 * 1024**2

TUNABLES = (
    "REFINEMENT_MEM_LIMIT",
    "REFINEMENT_CPUS",
    "DUCKDB_MEMORY_LIMIT",
    "DUCKDB_TEMP_DIRECTORY",
    "DUCKDB_MAX_TEMP_DIRECTORY_SIZE",
    "DUCKDB_THREADS",
)

REQUIRED_ENV_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)(?::)?\?[^}]*\}")
_SIZE_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([KMGT]?i?B)\s*$", re.IGNORECASE)
_UNITS = {
    "B": 1,
    "KB": 1000, "KIB": 1024,
    "MB": 1000**2, "MIB": 1024**2,
    "GB": 1000**3, "GIB": 1024**3,
    "TB": 1000**4, "TIB": 1024**4,
}


def _to_bytes(value: str) -> int:
    match = _SIZE_RE.match(str(value))
    assert match, f"not a DuckDB size literal: {value!r}"
    return int(float(match.group(1)) * _UNITS[match.group(2).upper()])


def _render(compose_files: list[Path], env_file: Path) -> dict:
    sources = "\n".join(p.read_text(encoding="utf-8") for p in compose_files)
    environ = os.environ.copy()
    for name in TUNABLES:
        environ.pop(name, None)
    for name in REQUIRED_ENV_RE.findall(sources):
        environ[name] = "compose-contract"
    environ.update(
        {
            "COMPOSE_PROJECT_NAME": "refinement-quota-contract",
            "AWS_ENV_FILE": str(env_file),
            "MODECISSIONS_CONTROL_ROOM_EVIDENCE_ENV_FILE": str(env_file),
            "GOLD_VERIFIER_DATABASE_URL_HOST_FILE": str(env_file),
            "SF_PRIVATE_KEY_HOST_PATH": "/dev/null",
        }
    )
    command = ["docker", "compose"]
    for path in compose_files:
        command.extend(["-f", str(path)])
    command.extend(["--env-file", str(env_file), "config", "--format", "json"])
    result = subprocess.run(
        command, cwd=ROOT, env=environ, capture_output=True, text=True,
        check=False, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["services"]["refinement"]


@pytest.fixture(scope="module")
def aws_refinement(tmp_path_factory: pytest.TempPathFactory) -> dict:
    empty = tmp_path_factory.mktemp("quota") / "empty.env"
    empty.touch()
    return _render([AWS_COMPOSE, AWS_CARTRIDGES_COMPOSE], empty)


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


def test_aws_container_is_bounded(aws_refinement: dict) -> None:
    mem = int(aws_refinement.get("mem_limit") or 0)

    assert mem > 0, (
        "refinement resolves to an unlimited container in the AWS compose. "
        "That is the 2026-09-20 configuration."
    )
    assert mem <= 3 * 1024**3, f"mem_limit {mem} is above the 3 GiB budget"
    assert float(aws_refinement.get("cpus") or 0) > 0, "no cpus ceiling"

    swap = int(aws_refinement.get("memswap_limit") or 0)
    assert swap == mem, (
        f"memswap_limit ({swap}) must equal mem_limit ({mem}) so the container "
        "gets no swap allowance on top of its RAM ceiling"
    )


def test_aws_duckdb_memory_limit_is_a_fraction_of_the_host(
    aws_refinement: dict,
) -> None:
    env = aws_refinement["environment"]
    duck = _to_bytes(env["DUCKDB_MEMORY_LIMIT"])
    mem = int(aws_refinement["mem_limit"])

    assert 0 < duck <= HOST_RAM_BYTES // 4, (
        f"DUCKDB_MEMORY_LIMIT resolves to {duck} bytes, more than a quarter of "
        f"the {HOST_RAM_BYTES}-byte host. One query can then starve the other "
        "containers."
    )
    assert duck <= mem // 2, (
        f"DUCKDB_MEMORY_LIMIT ({duck}) leaves no room inside mem_limit ({mem}) "
        "for the Python/Arrow copies of a result set."
    )


def test_aws_spill_is_pinned_and_capped(aws_refinement: dict) -> None:
    env = aws_refinement["environment"]

    directory = str(env["DUCKDB_TEMP_DIRECTORY"])
    assert directory.startswith("/"), (
        f"DUCKDB_TEMP_DIRECTORY resolves to {directory!r}; DuckDB requires an "
        "absolute path and its own default is the relative '.tmp'"
    )
    assert _to_bytes(env["DUCKDB_MAX_TEMP_DIRECTORY_SIZE"]) > 0, (
        "the spill cap resolves to nothing, so DuckDB keeps its default of "
        "~90% of the filesystem -- shared with Postgres"
    )


def test_aws_threads_fit_the_cpu_ceiling(aws_refinement: dict) -> None:
    threads = int(aws_refinement["environment"]["DUCKDB_THREADS"])
    cpus = float(aws_refinement["cpus"])

    assert 1 <= threads <= math.ceil(cpus), (
        f"DUCKDB_THREADS={threads} exceeds the {cpus} CPU ceiling"
    )


@pytest.mark.parametrize("key", TUNABLES[2:])
def test_aws_refinement_declares_duckdb_key_itself(key: str) -> None:
    doc = yaml.safe_load(AWS_COMPOSE.read_text(encoding="utf-8"))
    env = doc["services"]["refinement"].get("environment") or {}

    assert key in env, (
        f"refinement does not declare {key} in the AWS compose. Inheriting it "
        "from the shared .env is not enough: re-rendering that file, or "
        "dropping env_file, removes the ceiling with no test failing."
    )


def test_aws_env_example_documents_refinement_quotas() -> None:
    src = AWS_ENV_EXAMPLE.read_text(encoding="utf-8")
    for needle in (
        "REFINEMENT_MEM_LIMIT=3g",
        "REFINEMENT_CPUS=1.5",
        "DUCKDB_MEMORY_LIMIT=1GB",
        "DUCKDB_TEMP_DIRECTORY=/tmp/duckdb-spill",
        "DUCKDB_MAX_TEMP_DIRECTORY_SIZE=8GB",
        "DUCKDB_THREADS=2",
    ):
        assert needle in src, f"{needle} missing from the AWS .env.example"
