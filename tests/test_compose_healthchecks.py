from __future__ import annotations

import shlex
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE = REPO_ROOT / "infra" / "docker-compose.yml"


APP_SERVICES_WITH_PORTS = {
    "console": 8000,
    "workspace": 8001,
    "refinement": 8500,
    "vault": 8300,
    "mcp-infra": 8010,
}
APP_SERVICE_HEALTH_PATHS = {
    service: "/readyz" if service == "refinement" else "/healthz"
    for service in APP_SERVICES_WITH_PORTS
}


def _compose_doc():
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _healthcheck_command(service: str) -> str:
    cmd = _compose_doc()["services"][service]["healthcheck"]["test"]
    return " ".join(cmd) if isinstance(cmd, list) else str(cmd)


@pytest.mark.parametrize("service", sorted(APP_SERVICES_WITH_PORTS))
def test_app_service_has_healthcheck(service):
    doc = _compose_doc()
    svc = doc["services"][service]
    hc = svc.get("healthcheck")
    assert hc, f"{service} is missing a healthcheck block"
    assert "test" in hc, f"{service} healthcheck has no test field"


@pytest.mark.parametrize("service,port", sorted(APP_SERVICES_WITH_PORTS.items()))
def test_app_service_healthcheck_targets_healthz_on_correct_port(service, port):
    hc = _compose_doc()["services"][service]["healthcheck"]
    joined = _healthcheck_command(service)
    endpoint = APP_SERVICE_HEALTH_PATHS[service]
    assert f":{port}{endpoint}" in joined, (
        f"{service} healthcheck does not target {endpoint} on port {port}: "
        f"{joined!r}"
    )


def test_all_healthchecks_use_python_not_wget_or_curl():
    doc = _compose_doc()
    bad = []
    for svc in APP_SERVICES_WITH_PORTS:
        joined = _healthcheck_command(svc)
        if joined.startswith("CMD wget") or "CMD-SHELL wget" in joined:
            bad.append(f"{svc} uses wget but images don't ship it: {joined!r}")
        elif joined.startswith("CMD curl") or "CMD-SHELL curl" in joined:
            bad.append(f"{svc} uses curl but images don't ship it: {joined!r}")
    assert not bad, "\n".join(bad)


@pytest.mark.parametrize(
    "service,port,database",
    [
        ("postgres", 5432, "modecissions"),
        ("postgres_gold", 5433, "modecissions_gold"),
    ],
)
def test_postgres_healthchecks_use_tcp_listener(service, port, database):
    joined = _healthcheck_command(service)
    assert "pg_isready" in joined
    assert "-h 127.0.0.1" in joined
    assert f"-p {port}" in joined
    assert f"-d {database}" in joined


@pytest.mark.parametrize("service", sorted(APP_SERVICES_WITH_PORTS))
def test_app_service_healthcheck_has_start_period(service):
    hc = _compose_doc()["services"][service]["healthcheck"]
    assert hc.get("start_period"), (
        f"{service} healthcheck must declare start_period to absorb "
        f"the lifespan's startup latency"
    )


_INIT_SERVICES = {
    "airflow-init",
    "minio-init",
    "superset-init",
    "postgres_dev_seed",
}


def _long_running_services():
    doc = _compose_doc()
    return [name for name in doc.get("services", {}) if name not in _INIT_SERVICES]


def test_minio_init_is_classified_as_one_shot():
    service = _compose_doc()["services"]["minio-init"]
    assert "minio-init" in _INIT_SERVICES
    assert "minio-init" not in _long_running_services()
    assert service.get("restart") == "no"


@pytest.mark.parametrize("service", sorted(_long_running_services()))
def test_long_running_service_has_healthcheck(service):
    svc = _compose_doc()["services"][service]
    hc = svc.get("healthcheck")
    assert hc, (
        f"{service} is a long-running service but has no healthcheck — "
        f"docker compose ps will hide its real state"
    )
    assert hc.get("test"), f"{service} healthcheck has no test field"


def test_critical_local_dependencies_use_service_healthy():
    services = _compose_doc()["services"]
    critical_targets = {
        "airflow",
        "console",
        "mailhog",
        "mcp-infra",
        "minio",
        "postgres",
        "postgres_gold",
        "redis",
        "refinement",
        "vault",
        "workspace",
    }
    offenders: list[str] = []
    for service, body in services.items():
        deps = body.get("depends_on")
        if deps is None:
            continue
        if isinstance(deps, list):
            for target in deps:
                if target in critical_targets:
                    offenders.append(f"{service} -> {target}: bare depends_on")
            continue
        assert isinstance(deps, dict), f"{service} depends_on must be list or dict"
        for target, spec in deps.items():
            if target not in critical_targets:
                continue
            condition = spec.get("condition") if isinstance(spec, dict) else None
            if condition != "service_healthy":
                offenders.append(f"{service} -> {target}: {condition!r}")
    assert not offenders, (
        "Critical local dependencies must wait for service_healthy:\n  "
        + "\n  ".join(offenders)
    )


def test_service_healthy_dependencies_have_healthchecks():
    services = _compose_doc()["services"]
    offenders: list[str] = []
    for service, body in services.items():
        deps = body.get("depends_on") or {}
        if not isinstance(deps, dict):
            continue
        for target, spec in deps.items():
            if not isinstance(spec, dict) or spec.get("condition") != "service_healthy":
                continue
            target_body = services.get(target) or {}
            healthcheck = target_body.get("healthcheck")
            if not healthcheck or not healthcheck.get("test"):
                offenders.append(
                    f"{service} waits on {target}, but {target} has no healthcheck"
                )
    assert not offenders, "\n".join(offenders)


def _minio_init_command() -> str:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    service = compose["services"]["minio-init"]
    command = service.get("command")
    if isinstance(command, list):
        return " ".join(str(part) for part in command)
    return str(command or "")


def test_minio_init_does_not_depend_on_grep():
    tokens = shlex.split(_minio_init_command())
    assert "grep" not in tokens, (
        "minio-init must not call grep: the mc image does not ship it, "
        "and the init already fails closed on mc's own exit code"
    )


def test_minio_init_still_creates_the_bucket_and_enables_versioning():
    command = _minio_init_command()
    assert "mc alias set local" in command
    assert "mc mb --ignore-existing local/lakehouse" in command
    assert "mc version enable local/lakehouse" in command
    assert command.count("&&") >= 2
