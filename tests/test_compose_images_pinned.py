"""Sprint v1.43.2 (Claude B7) — every compose image must be pinned.

``:latest`` ships breaking changes silently between deploys. Both
the local compose and the AWS compose now use a real RELEASE tag
for MinIO. A regression-only test exists in
test_compose_aws_consistency.py for the AWS file; this one covers
the local compose and pins the invariant going forward.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml


REPO = Path(__file__).resolve().parents[1]
COMPOSE_LOCAL = REPO / "infra" / "docker-compose.yml"
COMPOSE_AWS   = REPO / "infra" / "terraform" / "deploy" / "docker-compose.aws.yml"


def _doc(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", [COMPOSE_LOCAL, COMPOSE_AWS],
                         ids=lambda p: p.name)
def test_no_compose_image_pins_latest(path):
    """No compose image may pin ``:latest``. Application images are
    released through GHCR tags; compose files must consume a versioned tag."""
    doc = _doc(path)
    bad: list[str] = []
    for name, svc in (doc.get("services") or {}).items():
        image = (svc or {}).get("image", "") or ""
        if not isinstance(image, str):
            continue
        if image.endswith(":latest"):
            bad.append(f"{path.name}::{name} → {image}")
    assert not bad, (
        "Compose services pinning :latest are non-reproducible:\n  "
        + "\n  ".join(bad)
    )


def test_minio_image_pinned_with_release_format():
    """MinIO must use the official RELEASE.YYYY-MM-DDTHH-MM-SSZ tag —
    the only contract MinIO offers for reproducible behaviour."""
    doc = _doc(COMPOSE_LOCAL)
    minio = (doc.get("services") or {}).get("minio") or {}
    image = minio.get("image", "")
    assert image, "minio service missing image: directive"
    assert image.startswith("ghcr.io/emmanuelnavaromero02-commits/omega-minio:RELEASE."), (
        f"minio image must use the RELEASE.YYYY-MM-DD… format, got {image!r}"
    )
    # Sanity: the tag matches the upstream format so a typo gets
    # caught here rather than at runtime via image-pull failure.
    assert re.match(
        r"ghcr\.io/emmanuelnavaromero02-commits/omega-minio:RELEASE\.\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z\b",
        image,
    ), f"minio tag does not match RELEASE format: {image!r}"


def test_minio_local_and_aws_use_same_release():
    """Drift between local and AWS images = different behaviour in
    staging vs prod. Both must pin the same RELEASE tag."""
    local_image = (_doc(COMPOSE_LOCAL)["services"]["minio"] or {}).get("image", "")
    aws_minio = (_doc(COMPOSE_AWS)["services"].get("minio") or {})
    # AWS compose may not declare a minio service if it relies on S3 +
    # IAM — in that case we don't require parity.
    if not aws_minio:
        pytest.skip("AWS compose doesn't ship its own minio service")
    aws_image = aws_minio.get("image", "")
    assert local_image == aws_image, (
        f"local compose pins {local_image!r} but AWS pins {aws_image!r}"
    )


@pytest.mark.parametrize("path", [COMPOSE_LOCAL, COMPOSE_AWS],
                         ids=lambda p: p.name)
def test_every_service_has_either_image_or_build(path):
    """Hardening: a service with neither image nor build can't start.
    Compose will error at runtime but a YAML lint catches it earlier."""
    doc = _doc(path)
    bad = []
    for name, svc in (doc.get("services") or {}).items():
        svc = svc or {}
        if not svc.get("image") and not svc.get("build"):
            bad.append(name)
    assert not bad, f"services without image or build: {bad}"
