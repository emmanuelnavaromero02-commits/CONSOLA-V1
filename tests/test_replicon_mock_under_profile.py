"""Sprint v1.21 (F3) — replicon-mock is gated behind the `dev` profile.

`make up` (no profile) must NOT start the mock. A misconfigured
production REPLICON_BASE_URL pointing at this service would otherwise
feed real ETL pipelines with fake records.
"""
from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE = REPO_ROOT / "infra" / "docker-compose.yml"


def _compose_doc():
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_replicon_mock_service_exists():
    doc = _compose_doc()
    assert "replicon-mock" in doc["services"], (
        "replicon-mock service was removed entirely. Dev workflow needs "
        "it; gate it via `profiles: [dev]` instead of deleting."
    )


def test_replicon_mock_has_dev_profile():
    """Without this gating, `make up` (no profile) starts the mock and
    silently shadows the real Replicon URL if the prod operator misnames
    REPLICON_BASE_URL."""
    svc = _compose_doc()["services"]["replicon-mock"]
    profiles = svc.get("profiles") or []
    assert "dev" in profiles, (
        f"replicon-mock must declare `profiles: [dev]` (got {profiles!r}). "
        f"This service serves synthetic data and must not start in production."
    )


def test_no_service_hard_depends_on_replicon_mock():
    """A `depends_on: replicon-mock` would defeat the profile gate —
    docker compose refuses to start the dependent service when the
    target is filtered out by profile.

    Airflow had three such dependencies (airflow-init, airflow,
    airflow-scheduler) that we deliberately removed in v1.21 F3.
    """
    doc = _compose_doc()
    offenders = []
    for name, svc in doc["services"].items():
        if name == "replicon-mock":
            continue
        deps = svc.get("depends_on")
        if not deps:
            continue
        # depends_on can be a list of names OR a dict of name → {condition: ...}
        targets = deps if isinstance(deps, list) else list(deps.keys())
        if "replicon-mock" in targets:
            offenders.append(name)
    assert not offenders, (
        "Services still hard-depend on replicon-mock, which is now "
        "behind `profiles: [dev]` and won't exist in prod boots:\n  "
        + "\n  ".join(offenders)
    )
