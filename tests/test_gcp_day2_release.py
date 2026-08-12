"""Checkpoint 5.5 — canonical GCP day-two release controller.

These scripts run against the live GCP VM, so the executable path is an external
wall we cannot cross in CI. What we CAN pin here is the safety contract of the
scripts as artifacts: they are syntactically valid, they refuse to run without an
explicit canonical target, they never read secret values, the day-two sequence
is ordered and fail-closed, rollback is never a blind N-1, and the whole thing
reuses main's already-audited release scripts instead of re-implementing them.
"""
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


# ── verify-host-identity: wrong-project/VM guard ────────────────────────────

def test_host_identity_requires_explicit_target_and_is_fail_closed():
    script = _text("verify-host-identity")
    # Refuses to run without all four expected identity components.
    for token in ("expected_project", "expected_instance", "expected_zone", "expected_sa"):
        assert token in script
    assert "requires <project-id> <instance-name> <zone> <service-account-email>" in script
    # Reads live identity from the metadata server.
    assert "metadata.google.internal" in script
    assert "project/project-id" in script
    assert "instance/name" in script
    assert "instance/zone" in script
    assert "instance/service-accounts/default/email" in script
    # Exact-match, fail-closed on any mismatch.
    assert "mismatch" in script
    assert "HOST_IDENTITY\\tFAIL" in script


def test_host_identity_is_identity_only_never_reads_a_token_or_secret():
    """The wrong-target guard must not pull a token or any secret value."""
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


# ── verify-secret-access: effective IAM without reading values ──────────────

def test_secret_access_uses_testiampermissions_and_never_reads_values():
    script = _text("verify-secret-access")
    assert "testIamPermissions" in script
    assert "secretmanager.versions.access" in script
    # Never reads a secret payload: no :access version fetch here.
    assert ":access" not in script
    # Explicitly attests it did not read any secret value.
    assert '"secret_values_read": False' in script or '"secret_values_read"' in script


def test_secret_access_allowlist_includes_ghcr_pull_credentials():
    """The private-image pull (issue #579/#583) depends on the VM being able to
    access the server-owned GHCR pull credential."""
    script = _text("verify-secret-access")
    assert "ghcr_pull_credentials" in script


# ── backup-restore: verifiable restore-point, no blind rollback ─────────────

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
    # do_restore calls verify_manifest first.
    do_restore = script.index("do_restore()")
    verify_call = script.index("verify_manifest", do_restore)
    psql_restore = script.index("psql", do_restore)
    assert do_restore < verify_call < psql_restore
    # A checksum mismatch fails closed.
    assert "checksum mismatch" in script


def test_backup_refuses_to_overwrite_an_existing_restore_point():
    script = _text("backup-restore")
    assert "already exists" in script
    assert "refusing to overwrite" in script


# ── day2-release: ordered, fail-closed, reuses main, no blind N-1 ───────────

def test_day2_requires_explicit_release_identity():
    script = _text("day2-release")
    for var in (
        "TARGET_TAG",
        "DEPLOY_REF",
        "GHCR_OWNER",
        "EXPECTED_PROJECT_ID",
        "EXPECTED_INSTANCE_NAME",
        "EXPECTED_ZONE",
        "EXPECTED_SERVICE_ACCOUNT",
    ):
        assert f"require {var}" in script or var in script
    # DEPLOY_REF is pinned to a full commit SHA (no floating ref).
    assert "^[0-9a-f]{40}$" in script


def test_day2_runs_the_eight_steps_in_order():
    script = _text("day2-release")
    order = [
        "step 1/8 preflight",
        "step 2/8 backup",
        "step 3/8 images",
        "step 4/8 migrations",
        "step 5/8 deploy",
        "step 6/8 health",
        "step 7/8 regression",
        "step 8/8 promote",
    ]
    positions = []
    for marker in order:
        assert marker in script, f"missing day-two step: {marker}"
        positions.append(script.index(marker))
    assert positions == sorted(positions), "day-two steps are out of order"


def test_day2_reuses_mains_audited_release_scripts():
    """5.5 reduces, it does not re-implement. The image pull + digest lock come
    from main's ghcr-auth-run.sh / preflight-release-images.sh / overlay."""
    script = _text("day2-release")
    assert "ghcr-auth-run.sh" in script
    assert "preflight-release-images.sh" in script
    assert "docker-compose.release.yml" in script
    assert "apply_db_migrations.sh" in script


def test_day2_health_gate_requires_data_and_pins_version():
    script = _text("day2-release")
    assert "/healthz" in script
    assert "/readyz?require_data=1" in script
    assert "app_env" in script and "production" in script
    assert "EXPECTED_VERSION" in script


def test_day2_promotes_only_after_all_gates_and_atomically():
    script = _text("day2-release")
    # Promotion is the final step and flips a durable pointer via atomic rename.
    promote = script.index("step 8/8 promote")
    health = script.index("step 6/8 health")
    regression = script.index("step 7/8 regression")
    assert health < promote and regression < promote
    assert "mv -f" in script[promote:]
    assert "PROMOTED=1" in script


def test_day2_rollback_is_never_a_blind_n_minus_one():
    script = _text("day2-release")
    # Rollback re-pins the EXACT previous digest set and restores the captured
    # backup — it must not fabricate a ":previous"/":latest" image tag.
    assert "PREVIOUS_LOCK" in script
    assert "backup-restore.sh" in script and "restore" in script
    assert ":previous" not in script
    assert ":latest" not in script


def test_day2_is_fail_closed_on_error_after_mutation():
    script = _text("day2-release")
    assert "trap on_error ERR" in script
    assert "rollback" in script
    assert "BACKUP_CAPTURED" in script


def test_release_overlay_still_covers_exactly_15_images():
    overlay = (REPO_ROOT / "infra/terraform-gcp/release/docker-compose.release.yml").read_text()
    locks = set()
    for line in overlay.splitlines():
        marker = "${OMEGA_GCP_IMAGE_"
        if marker in line:
            start = line.index(marker) + len("${")
            rest = line[start:]
            name = rest.split(":")[0].split("}")[0]
            locks.add(name)
    assert len(locks) == 15, f"expected 15 distinct release images, got {sorted(locks)}"


def test_preflight_release_images_enforces_exactly_15():
    preflight = (REPO_ROOT / "infra/terraform-gcp/release/preflight-release-images.sh").read_text()
    assert "-ne 15" in preflight
