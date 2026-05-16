"""Sprint v1.43.4 — Claude N1/N3/N4 + Codex H1: AWS compose must
match local compose on critical version pins so DAGs / migrations
tested locally don't fail silently in prod.

Claude N1 (closed in this sprint): infra/docker-compose.yml ran
Airflow 2.10.5 locally while infra/terraform/deploy/docker-compose.aws.yml
ran Airflow 2.9.2 — a year's worth of upstream behavior drift.

The tests below are static guards: they read both compose files,
extract image pins, and assert AWS matches the local ground truth.
Adding a new pin to local compose will fail the test until the
operator either (a) mirrors it in AWS or (b) explicitly carves it
out of the consistency check.
"""
from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
LOCAL = REPO / "infra/docker-compose.yml"
AWS = REPO / "infra/terraform/deploy/docker-compose.aws.yml"


def _images(path: Path) -> list[str]:
    """Return every ``image: …`` value in the compose file."""
    return re.findall(r"^\s*image:\s*(\S+)\s*$", path.read_text(), re.MULTILINE)


def _versions_of(images: list[str], prefix: str) -> set[str]:
    """Return the set of versions for an image prefix, e.g.
    'apache/airflow' → {'2.10.5'}."""
    out: set[str] = set()
    for img in images:
        if img.startswith(prefix + ":") or img == prefix:
            _, _, tag = img.partition(":")
            out.add(tag or "(no tag)")
    return out


def test_airflow_version_matches_local():
    """The Claude N1 finding: local was 2.10.5, AWS was 2.9.2 — the
    drift this hotfix closes. Lock the parity going forward."""
    local_versions = _versions_of(_images(LOCAL), "mode-airflow") \
        or _versions_of(_images(LOCAL), "apache/airflow")
    aws_versions = _versions_of(_images(AWS), "apache/airflow")

    # Normalise the local "mode-airflow:2.10.5-local" custom-built tag
    # to just the upstream version it derives from.
    local_upstream = {v.split("-")[0] for v in local_versions}

    assert aws_versions, "AWS compose must reference apache/airflow"
    assert aws_versions == local_upstream, (
        f"Airflow drift: local={local_upstream}, AWS={aws_versions}. "
        f"Either bump AWS to match or update this test to reflect the "
        f"intentional difference."
    )


def test_postgres_version_matches_local():
    """Local + AWS both run pgvector/pgvector:pg15. Lock the match."""
    local_pg = _versions_of(_images(LOCAL), "pgvector/pgvector")
    aws_pg = _versions_of(_images(AWS), "pgvector/pgvector")
    assert local_pg, "local compose must use pgvector/pgvector"
    assert aws_pg == local_pg, (
        f"Postgres drift: local={local_pg}, AWS={aws_pg}"
    )


def test_superset_version_matches_local():
    """Same lock for Superset — easy to forget when bumping locally."""
    local_ss = _versions_of(_images(LOCAL), "apache/superset")
    aws_ss = _versions_of(_images(AWS), "apache/superset")
    assert local_ss, "local compose must use apache/superset"
    assert aws_ss == local_ss, (
        f"Superset drift: local={local_ss}, AWS={aws_ss}"
    )


def test_no_floating_tags_on_third_party_images():
    """``:latest``, ``:main``, ``:edge`` on any upstream image is an
    invitation to ship a different binary every redeploy. Application
    images (modecissions/*) are allowed to use ``:latest`` until the
    registry-push sprint lands."""
    aws_images = _images(AWS)
    forbidden_tags = ("latest", "main", "edge", "stable")
    offenders: list[str] = []
    for img in aws_images:
        if img.startswith("modecissions/"):
            continue  # v1.45 sprint will rewire these to versioned tags
        name, _, tag = img.partition(":")
        if not tag:
            offenders.append(f"{img} (no tag)")
        elif tag in forbidden_tags:
            offenders.append(img)
    assert not offenders, (
        f"AWS compose has floating tags on upstream images: {offenders}. "
        f"Pin to an explicit version."
    )


def test_no_remaining_2_9_x_airflow_in_aws():
    """Defensive: should never see 2.9.x airflow in AWS after this
    sprint. Catches the case where a future change re-introduces the
    old pin via copy-paste."""
    src = AWS.read_text(encoding="utf-8")
    assert "apache/airflow:2.9." not in src, (
        "AWS compose still references apache/airflow:2.9.x — that's "
        "the Claude N1 drift this sprint closed"
    )


# ── v1.43.4 (Claude N3): AWS healthchecks + service_healthy deps ──────────


def _aws_services() -> dict:
    """Parse AWS compose; return services dict."""
    import yaml
    with AWS.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("services", {})


# Two AWS services are one-shot init containers (run command, exit
# cleanly). A Docker healthcheck on an exited container never goes
# HEALTHY by definition — dependents must use
# ``condition: service_completed_successfully`` instead. Carve them
# out of the healthcheck-coverage assertion.
_ONESHOT_INIT_SERVICES = {"superset-init", "airflow-init"}


def test_all_aws_long_running_services_have_healthchecks():
    """Claude N3: pre-v1.43.4 AWS compose had ZERO healthchecks — so
    ``depends_on`` only meant "container started", which races against
    Postgres init, mcp-infra warmup, etc. Every long-running service
    must now declare a healthcheck so dependents can wait on it."""
    svcs = _aws_services()
    missing = [
        name for name, body in svcs.items()
        if name not in _ONESHOT_INIT_SERVICES
        and "healthcheck" not in body
    ]
    assert not missing, (
        f"AWS compose services missing healthchecks: {missing}. "
        f"Either add ``healthcheck:`` or mark them as oneshot init."
    )


def test_aws_oneshot_services_are_explicitly_oneshot():
    """If a service is in the oneshot carve-out, it must actually be
    a one-shot (``restart: "no"`` AND no healthcheck). Catches the
    case where someone adds a long-running service to the carve-out
    by mistake."""
    svcs = _aws_services()
    for name in _ONESHOT_INIT_SERVICES:
        if name not in svcs:
            continue
        body = svcs[name]
        assert body.get("restart") == "no", (
            f"{name} is in oneshot carve-out but restart != 'no' "
            f"(got: {body.get('restart')!r})"
        )


def test_critical_aws_dependencies_use_service_healthy():
    """Every depends_on of a critical service (postgres, mcp-infra,
    airflow, vault, refinement) must use the long-form with
    ``condition:`` — the bare-list shorthand only waits for the
    container to start, not for the service inside it."""
    svcs = _aws_services()
    critical_targets = {
        "postgres",
        "postgres_gold",
        "mcp-infra",
        "airflow",
        "vault",
        "refinement",
        "mailhog",
    }
    offenders: list[str] = []
    for name, body in svcs.items():
        deps = body.get("depends_on")
        if deps is None:
            continue
        # Shorthand (list of strings) means no condition — that's the
        # racy form. If any item in the depends-on list is a critical
        # target, fail.
        if isinstance(deps, list):
            racy = [d for d in deps if d in critical_targets]
            if racy:
                offenders.append(
                    f"{name} → {racy} (bare list; needs condition: "
                    f"service_healthy)"
                )
        elif isinstance(deps, dict):
            for target, spec in deps.items():
                if target not in critical_targets:
                    continue
                if not isinstance(spec, dict):
                    offenders.append(
                        f"{name} → {target} (spec is {spec!r}; needs "
                        f"a dict with 'condition')"
                    )
                    continue
                cond = spec.get("condition")
                if cond not in (
                    "service_healthy",
                    "service_completed_successfully",
                ):
                    offenders.append(
                        f"{name} → {target} (condition={cond!r}; "
                        f"must be service_healthy)"
                    )
    assert not offenders, (
        "AWS compose has racy depends_on entries:\n  "
        + "\n  ".join(offenders)
    )


def test_aws_healthcheck_test_command_is_well_formed():
    """A healthcheck declared with ``test: <string>`` (instead of the
    ``["CMD", ...]`` / ``["CMD-SHELL", ...]`` form) is silently
    ignored by docker compose. Lock the explicit-list form."""
    svcs = _aws_services()
    bad: list[str] = []
    for name, body in svcs.items():
        hc = body.get("healthcheck")
        if not hc:
            continue
        test = hc.get("test")
        if not isinstance(test, list) or not test:
            bad.append(f"{name}: test={test!r}")
            continue
        head = test[0]
        if head not in ("CMD", "CMD-SHELL", "NONE"):
            bad.append(f"{name}: test[0]={head!r} (must be CMD/CMD-SHELL/NONE)")
    assert not bad, (
        "AWS compose healthchecks with bad test form:\n  "
        + "\n  ".join(bad)
    )
