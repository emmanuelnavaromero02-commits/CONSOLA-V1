"""Sprint v1.21 (F2) — every app service in compose has a healthcheck.

Pins the F2 fix: console / workspace / refinement / vault / mcp-infra
must each declare a healthcheck so dependent services can wait on
`condition: service_healthy` instead of `service_started`. The probe
itself is asserted to use python3 -c (the images are python:3.12-slim
and don't ship with wget/curl).
"""
from __future__ import annotations

import shlex
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE = REPO_ROOT / "infra" / "docker-compose.yml"


APP_SERVICES_WITH_PORTS = {
    "console":    8000,
    "workspace":  8001,
    "refinement": 8500,
    "vault":      8300,
    "mcp-infra":  8010,
}


def _compose_doc():
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _healthcheck_command(service: str) -> str:
    cmd = _compose_doc()["services"][service]["healthcheck"]["test"]
    return " ".join(cmd) if isinstance(cmd, list) else str(cmd)


@pytest.mark.parametrize("service", sorted(APP_SERVICES_WITH_PORTS))
def test_app_service_has_healthcheck(service):
    """Every in-scope app service declares a healthcheck block."""
    doc = _compose_doc()
    svc = doc["services"][service]
    hc = svc.get("healthcheck")
    assert hc, f"{service} is missing a healthcheck block"
    assert "test" in hc, f"{service} healthcheck has no test field"


@pytest.mark.parametrize("service,port", sorted(APP_SERVICES_WITH_PORTS.items()))
def test_app_service_healthcheck_targets_healthz_on_correct_port(service, port):
    """The probe must hit /healthz on the service's own port. A wrong
    port silently fails forever — start_period: 20s masks it until
    retries are exhausted, then dependent services never start."""
    hc = _compose_doc()["services"][service]["healthcheck"]
    # `test:` can be either ["CMD", arg1, arg2, …] or ["CMD-SHELL", "string"]
    joined = _healthcheck_command(service)
    assert "/healthz" in joined, (
        f"{service} healthcheck does not call /healthz: {joined!r}"
    )
    assert f":{port}/" in joined, (
        f"{service} healthcheck does not target port {port}: {joined!r}"
    )


def test_all_healthchecks_use_python_not_wget_or_curl():
    """Service images are python:3.12-slim — no wget, no curl. The probe
    must use the python3 binary that's guaranteed to be present.

    A future migration to a distro image with curl is fine; this test
    will fail visibly so the change is noticed."""
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
    """Init containers connect through service hostnames over TCP.
    Socket-only pg_isready can go green while Postgres is still in
    its first-boot temporary server, racing airflow-init/superset-init."""
    joined = _healthcheck_command(service)
    assert "pg_isready" in joined
    assert "-h 127.0.0.1" in joined
    assert f"-p {port}" in joined
    assert f"-d {database}" in joined


@pytest.mark.parametrize("service", sorted(APP_SERVICES_WITH_PORTS))
def test_app_service_healthcheck_has_start_period(service):
    """A start_period gives the lifespan time to finish (DB pool setup,
    crypto key validation, MCP registry scan) before the first probe
    counts toward retries. Without it, fast probes during boot race
    the lifespan and the container loops forever as `(starting)`."""
    hc = _compose_doc()["services"][service]["healthcheck"]
    assert hc.get("start_period"), (
        f"{service} healthcheck must declare start_period to absorb "
        f"the lifespan's startup latency"
    )


# ── Sprint v1.41.1: blanket coverage for every long-running service ─────────

# Init / one-shot containers exit with status 0 by design; docker compose
# represents their terminal state as Exited (0), not a healthy/unhealthy
# pair, so a healthcheck on these would only confuse compose ps.
_INIT_SERVICES = {"airflow-init", "superset-init", "postgres_dev_seed"}


def _long_running_services():
    doc = _compose_doc()
    return [
        name for name in doc.get("services", {})
        if name not in _INIT_SERVICES
    ]


@pytest.mark.parametrize("service", sorted(_long_running_services()))
def test_long_running_service_has_healthcheck(service):
    """Every long-running service must declare a healthcheck so that
    `docker compose ps` reflects real health (healthy / unhealthy /
    starting) instead of falling back to plain `Up`. Init/one-shot
    services are excluded — they exit by design."""
    svc = _compose_doc()["services"][service]
    hc = svc.get("healthcheck")
    assert hc, (
        f"{service} is a long-running service but has no healthcheck — "
        f"docker compose ps will hide its real state"
    )
    assert hc.get("test"), f"{service} healthcheck has no test field"


def test_critical_local_dependencies_use_service_healthy():
    """Critical local dependencies must not regress to service_started."""
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
    """Every dependency waited on as healthy must expose a real healthcheck."""
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
                offenders.append(f"{service} waits on {target}, but {target} has no healthcheck")
    assert not offenders, "\n".join(offenders)


# ── minio-init: no shelling out to binaries the image lacks ──────────────────

# Same failure class as the healthcheck probes above, one service over. The
# init chained `| grep -q Enabled` after `mc version enable`, and the mc image
# does not ship grep (it is not part of coreutils). The step exited 127 *after*
# the bucket and versioning were already correct, which took minio-init down,
# vault with it, and every application service after that.
#
# Asserted as a property, not a line: the init may use whatever mc subcommands
# it needs, but it must not depend on external utilities that the image is free
# to stop shipping.
_MC_IMAGE_MISSING = ("grep", "awk", "sed", "curl", "wget", "jq")


def _minio_init_command() -> str:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    service = compose["services"]["minio-init"]
    command = service.get("command")
    if isinstance(command, list):
        return " ".join(str(part) for part in command)
    return str(command or "")


@pytest.mark.parametrize("binary", _MC_IMAGE_MISSING)
def test_minio_init_does_not_depend_on_absent_utilities(binary):
    tokens = shlex.split(_minio_init_command())
    assert binary not in tokens, (
        f"minio-init must not call {binary}: the mc image does not ship it, "
        "and the init already fails closed on mc's own exit code"
    )


def test_minio_init_still_creates_the_bucket_and_enables_versioning():
    """The init must keep doing its job — this is not a licence to drop steps."""
    command = _minio_init_command()
    assert "mc alias set local" in command
    assert "mc mb --ignore-existing local/lakehouse" in command
    assert "mc version enable local/lakehouse" in command
    # Chained with && so any failing step fails the container.
    assert command.count("&&") >= 2
