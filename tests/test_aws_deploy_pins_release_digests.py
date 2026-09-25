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
    import importlib.util

    if str(REPO / "scripts") not in sys.path:
        sys.path.insert(0, str(REPO / "scripts"))
    spec = importlib.util.spec_from_file_location("_omega_deploy_main_aws", DEPLOY)
    module = importlib.util.module_from_spec(spec)
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


def test_release_publishes_by_digest_only() -> None:
    source = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert "push-by-digest=true" in source, (
        "the release no longer publishes by digest; the deploy's overlay may be "
        "unnecessary, or may now conflict with a real tag"
    )


def test_compose_files_reference_images_by_tag() -> None:
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
    resolve_release_manifest = _deploy_module().resolve_release_manifest

    with pytest.raises(SystemExit) as excinfo:
        resolve_release_manifest(None, None, deploy_ref="a" * 40, image_tag="v9.9.9-beta")

    assert "OMEGA_RELEASE_MANIFEST" in str(excinfo.value)


def test_overlay_pins_every_service_the_deploy_recreates() -> None:
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
    release_manifest_overlay = _deploy_module().release_manifest_overlay

    overlay = yaml.safe_load(release_manifest_overlay(_manifest_fixture()))
    refs = {
        name: overlay["services"][name]["image"]
        for name in ("airflow", "airflow-init", "airflow-scheduler")
    }

    assert len(set(refs.values())) == 1, f"airflow services disagree: {refs}"


def test_overlay_does_not_disable_the_pull() -> None:
    release_manifest_overlay = _deploy_module().release_manifest_overlay

    overlay = yaml.safe_load(release_manifest_overlay(_manifest_fixture()))

    for name, service in overlay["services"].items():
        assert "pull_policy" not in service, (
            f"{name} sets pull_policy; the deploy relies on `docker compose "
            "pull` succeeding as its proof that every image exists"
        )


def test_remote_script_verifies_and_uses_the_overlay() -> None:
    source = DEPLOY.read_text(encoding="utf-8")

    assert "IMAGES_OVERLAY_SHA256" in source, "the overlay is shipped unverified"
    assert "-f omega-release-images.yml" in source, (
        "the overlay is written but never added to COMPOSE_FILES"
    )
    assert "every OMEGA image is digest-pinned" in source, (
        "the deploy does not assert, after pulling, that no tag reference remains"
    )


@pytest.mark.parametrize(
    ("image", "expected"),
    [("ghcr.io/o/console@sha256:" + "a" * 64, "0"), ("ghcr.io/o/console:v1", "1")],
)
def test_pin_check_survives_pipefail(image: str, expected: str) -> None:
    import subprocess

    sys.path.insert(0, str(REPO / "scripts"))
    import deploy_main_aws

    script = deploy_main_aws._remote_deploy_script(
        artifact_bucket="b", artifact_key="k", artifact_sha256="0" * 64, deploy_ref="a" * 40,
        image_tag="v0.0.0", version="0.0.0", run_migrations=False,
        images_overlay_b64="", images_overlay_sha256="0" * 64,
    )
    check = next(line for line in script.splitlines() if line.startswith("unpinned="))
    compose = json.dumps({"services": {"console": {"image": image}}}, indent=2)
    stub = f"docker() {{ cat <<'JSON'\n{compose}\nJSON\n}}\n"
    result = subprocess.run(
        ["bash", "-c", f"set -euo pipefail\nCOMPOSE_FILES=x\n{stub}{check}\nprintf '%s' \"$unpinned\""],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected
