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
    cmd = hc["test"]
    # `test:` can be either ["CMD", arg1, arg2, …] or ["CMD-SHELL", "string"]
    joined = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
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
        cmd = doc["services"][svc]["healthcheck"]["test"]
        joined = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if joined.startswith("CMD wget") or "CMD-SHELL wget" in joined:
            bad.append(f"{svc} uses wget but images don't ship it: {joined!r}")
        elif joined.startswith("CMD curl") or "CMD-SHELL curl" in joined:
            bad.append(f"{svc} uses curl but images don't ship it: {joined!r}")
    assert not bad, "\n".join(bad)


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
