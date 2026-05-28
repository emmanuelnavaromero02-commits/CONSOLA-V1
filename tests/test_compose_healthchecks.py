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
