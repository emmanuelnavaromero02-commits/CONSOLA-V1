from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
GCP_DIR = REPO_ROOT / "scripts" / "gcp"

SCRIPTS = {
    "verify-host-identity": GCP_DIR / "verify-host-identity.sh",
    "verify-secret-access": GCP_DIR / "verify-secret-access.sh",
    "backup-restore": GCP_DIR / "backup-restore.sh",
    "day2-release": GCP_DIR / "day2-release.sh",
}


def _text(name: str) -> str:
    return SCRIPTS[name].read_text(encoding="utf-8")


@pytest.mark.parametrize("name", sorted(SCRIPTS))
def test_script_exists_and_is_syntactically_valid(name):
    path = SCRIPTS[name]
    assert path.exists(), f"{path} is missing"
    result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, f"{name} has a bash syntax error:\n{result.stderr}"


def test_host_identity_requires_explicit_target_and_is_fail_closed():
    script = _text("verify-host-identity")
    for token in ("expected_project", "expected_instance", "expected_zone", "expected_sa"):
        assert token in script
    assert "requires <project-id> <instance-name> <zone> <service-account-email>" in script
    assert "metadata.google.internal" in script
    assert "project/project-id" in script
    assert "instance/name" in script
    assert "instance/zone" in script
    assert "instance/service-accounts/default/email" in script
    assert "mismatch" in script
    assert "HOST_IDENTITY\\tFAIL" in script


def test_host_identity_is_identity_only_never_reads_a_token_or_secret():
    script = _text("verify-host-identity")
    assert "service-accounts/default/token" not in script
    assert ":access" not in script
    assert "token_read=false" in script


def test_host_identity_rejects_missing_arguments_at_runtime():
    result = subprocess.run(
        ["bash", str(SCRIPTS["verify-host-identity"])],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "must be declared explicitly" in result.stderr


def test_secret_access_uses_testiampermissions_and_never_reads_values():
    script = _text("verify-secret-access")
    assert "testIamPermissions" in script
    assert "secretmanager.versions.access" in script
    assert ":access" not in script
    assert '"secret_values_read": False' in script or '"secret_values_read"' in script


def test_secret_access_allowlist_includes_ghcr_pull_credentials():
    script = _text("verify-secret-access")
    assert "ghcr_pull_credentials" in script


def test_backup_covers_both_canonical_databases():
    script = _text("backup-restore")
    assert "modecissions" in script
    assert "modecissions_gold" in script
    assert "pg_dump" in script


def test_backup_writes_a_sha256_manifest():
    script = _text("backup-restore")
    assert "manifest" in script
    assert "sha256_of" in script
    assert "SHA256SUMS" in script or "manifest.tsv" in script


def test_restore_verifies_the_manifest_before_touching_the_database():
    script = _text("backup-restore")
    do_restore = script.index("do_restore()")
    verify_call = script.index("verify_manifest", do_restore)
    psql_restore = script.index("psql", do_restore)
    assert do_restore < verify_call < psql_restore
    assert "checksum mismatch" in script


def test_backup_refuses_to_overwrite_an_existing_restore_point():
    script = _text("backup-restore")
    assert "already exists" in script
    assert "refusing to overwrite" in script


def test_day2_is_a_fail_closed_deprecation_stub():
    script = _text("day2-release")
    assert "gcp-canonical-deploy.sh <target-tag> <deploy-ref-40hex>" in script
    assert "disabled and performs no action" in script
    assert "exit 64" in script

    for forbidden in (
        "docker compose",
        "pg_dump",
        "backup-restore.sh",
        "apply_db_migrations.sh",
        "ghcr-auth-run.sh",
        "gcloud",
        "curl",
    ):
        assert forbidden not in script


def test_day2_exits_before_accepting_or_using_release_identity():
    result = subprocess.run(
        ["bash", str(SCRIPTS["day2-release"])],
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "TARGET_TAG": "v999.999.999",
            "DEPLOY_REF": "f" * 40,
        },
    )
    assert result.returncode == 64
    assert "gcp-canonical-deploy.sh" in result.stderr
    assert result.stdout == ""


def test_release_overlay_still_covers_exactly_16_images():
    overlay = (REPO_ROOT / "infra/terraform-gcp/release/docker-compose.release.yml").read_text()
    locks = set()
    for line in overlay.splitlines():
        marker = "${OMEGA_GCP_IMAGE_"
        if marker in line:
            start = line.index(marker) + len("${")
            rest = line[start:]
            name = rest.split(":")[0].split("}")[0]
            locks.add(name)
    assert len(locks) == 16, f"expected 16 distinct release images, got {sorted(locks)}"


def test_preflight_release_images_enforces_exactly_16():
    preflight = (REPO_ROOT / "infra/terraform-gcp/release/preflight-release-images.sh").read_text()
    assert "-ne 16" in preflight
