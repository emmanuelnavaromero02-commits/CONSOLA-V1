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
