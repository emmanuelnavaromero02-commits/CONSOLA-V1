"""Every OMEGA image the AWS deploy pulls must be a digest the release signed.

On 2026-09-23 `deploy-main-aws` reached production and died at `pull app
images`:

    failed to resolve reference "ghcr.io/.../inegi:v1.45.231-beta": not found

The release builds with `push-by-digest=true`, so GHCR never receives the
`vX.Y.Z` tag, while the compose files ask for `image: ghcr.io/.../console:
${IMAGE_TAG}`. The two halves asserted opposite things and nothing compared
them, so CI was green from end to end while the deploy could not pull a single
image.

GCP hit the identical failure first, on v1.45.224-beta, and #635 fixed it by
pinning digests from the signed release manifest. AWS was never given the same
treatment, and there was no dry run to catch it, so it landed in production.

These tests hold the two halves together: the release's publish mode, the
deploy's image references, and the overlay that bridges them.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
RELEASE_WORKFLOW = REPO / ".github" / "workflows" / "release.yml"
AWS_COMPOSE = REPO / "infra" / "terraform" / "deploy" / "docker-compose.aws.yml"
CARTRIDGES_COMPOSE = REPO / "infra" / "terraform" / "deploy" / "docker-compose.cartridges.yml"
DEPLOY = REPO / "scripts" / "deploy_main_aws.py"

def _deploy_module():
    """Load the deploy script by path.

    Importing it by name is fragile here: the suite manipulates sys.path, so a
    module-level insert does not survive collection. Loading from the file is
    deterministic and keeps the test honest about which file it is asserting on.
    """
    import importlib.util

    if str(REPO / "scripts") not in sys.path:
        sys.path.insert(0, str(REPO / "scripts"))
    spec = importlib.util.spec_from_file_location("_omega_deploy_main_aws", DEPLOY)
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves cls.__module__ through sys.modules; without this the
    # @dataclass definitions in the deploy script raise on exec.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module

GHCR_IMAGE = re.compile(r"ghcr\.io/\$\{GHCR_OWNER[^}]*\}/([a-z0-9._-]+):")


def _compose_images(path: Path) -> dict[str, str]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {
        name: str(service["image"])
        for name, service in (doc.get("services") or {}).items()
        if isinstance(service, dict) and "ghcr.io" in str(service.get("image", ""))
    }


def _manifest_fixture() -> dict:
    """A manifest shaped exactly like a real one, with by_service resolved."""
    import importlib.util
    if str(REPO / 'scripts') not in sys.path:
        sys.path.insert(0, str(REPO / 'scripts'))
    spec = importlib.util.spec_from_file_location('_omega_rde', REPO / 'scripts' / 'release_digest_env.py')
    rde = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = rde
    spec.loader.exec_module(rde)
    CANONICAL_SERVICES = rde.CANONICAL_SERVICES

    owner = "emmanuelnavaromero02-commits"
    by_service = {
        service: f"ghcr.io/{owner}/{service}@sha256:{i:064x}"
        for i, service in enumerate(CANONICAL_SERVICES, start=1)
    }
    return {"release_tag": "v9.9.9-beta", "source_sha": "a" * 40, "by_service": by_service}


# ── the gap itself ─────────────────────────────────────────────────────────


def test_release_publishes_by_digest_only() -> None:
    """The premise. If this ever stops being true, revisit the whole design."""
    source = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert "push-by-digest=true" in source, (
        "the release no longer publishes by digest; the deploy's overlay may be "
        "unnecessary, or may now conflict with a real tag"
    )


def test_compose_files_reference_images_by_tag() -> None:
    """The other half. The compose asks for a tag the registry never receives."""
    tagged = {
        **_compose_images(AWS_COMPOSE),
        **_compose_images(CARTRIDGES_COMPOSE),
    }

    assert tagged, "no ghcr images found; the compose layout changed"
    assert all("${IMAGE_TAG" in ref for ref in tagged.values()), (
        "some compose services no longer use IMAGE_TAG; the overlay may not "
        "cover them"
    )


def test_deploy_requires_the_signed_manifest() -> None:
    """A deploy without a manifest must refuse, not fall back to the tag."""
    resolve_release_manifest = _deploy_module().resolve_release_manifest

    with pytest.raises(SystemExit) as excinfo:
        resolve_release_manifest(None, None, deploy_ref="a" * 40, image_tag="v9.9.9-beta")

    assert "OMEGA_RELEASE_MANIFEST" in str(excinfo.value)


# ── the overlay is the bridge ──────────────────────────────────────────────


def test_overlay_pins_every_service_the_deploy_recreates() -> None:
    """The invariant: nothing the deploy pulls may resolve by tag."""
    release_manifest_overlay = _deploy_module().release_manifest_overlay

    overlay = yaml.safe_load(release_manifest_overlay(_manifest_fixture()))
    pinned = overlay["services"]

    compose_services = set(_compose_images(AWS_COMPOSE)) | set(_compose_images(CARTRIDGES_COMPOSE))
    missing = sorted(compose_services - set(pinned))

    assert not missing, (
        f"these compose services would still be pulled by tag: {missing}. "
        "Every OMEGA image the deploy touches must be pinned by the overlay."
    )


def test_every_overlay_reference_is_a_digest() -> None:
    release_manifest_overlay = _deploy_module().release_manifest_overlay

    overlay = yaml.safe_load(release_manifest_overlay(_manifest_fixture()))

    for name, service in overlay["services"].items():
        assert "@sha256:" in service["image"], f"{name} is not digest-pinned"
        assert ":v" not in service["image"].split("@")[0], f"{name} carries a tag"


def test_one_airflow_image_backs_its_three_services() -> None:
    """A real trap: airflow, airflow-init and airflow-scheduler share an image."""
    release_manifest_overlay = _deploy_module().release_manifest_overlay

    overlay = yaml.safe_load(release_manifest_overlay(_manifest_fixture()))
    refs = {
        name: overlay["services"][name]["image"]
        for name in ("airflow", "airflow-init", "airflow-scheduler")
    }

    assert len(set(refs.values())) == 1, f"airflow services disagree: {refs}"


def test_overlay_does_not_disable_the_pull() -> None:
    """`pull_policy: never` would silently remove the existence proof.

    The pull is what proves every image is present before the deploy stops
    services and runs migrations; the comment at that step says so. Pinning the
    digest must make that proof exact, not skip it.
    """
    release_manifest_overlay = _deploy_module().release_manifest_overlay

    overlay = yaml.safe_load(release_manifest_overlay(_manifest_fixture()))

    for name, service in overlay["services"].items():
        assert "pull_policy" not in service, (
            f"{name} sets pull_policy; the deploy relies on `docker compose "
            "pull` succeeding as its proof that every image exists"
        )


def test_remote_script_verifies_and_uses_the_overlay() -> None:
    """The overlay must reach the host intact and actually be in play."""
    source = DEPLOY.read_text(encoding="utf-8")

    assert "IMAGES_OVERLAY_SHA256" in source, "the overlay is shipped unverified"
    assert "-f omega-release-images.yml" in source, (
        "the overlay is written but never added to COMPOSE_FILES"
    )
    assert "every OMEGA image is digest-pinned" in source, (
        "the deploy does not assert, after pulling, that no tag reference remains"
    )
