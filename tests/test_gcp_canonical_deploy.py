"""The reproducible GCP canonical deploy driver — structural safety contract.

The driver deploys to the canonical GCP writer, so it cannot be exercised in CI.
What we pin here is that it stays fail-closed, backs up before mutating, never
targets AWS, and keeps the dry-run non-destructive — so a review catches a
regression before a human runs it against production.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LOCAL = REPO / "scripts" / "gcp" / "gcp-canonical-deploy.sh"
REMOTE = REPO / "scripts" / "gcp" / "gcp-canonical-deploy-remote.sh"
RUNBOOK = REPO / "docs" / "runbook" / "gcp-canonical-deploy.md"


@pytest.mark.parametrize("path", [LOCAL, REMOTE])
def test_scripts_exist_and_parse(path):
    assert path.exists(), f"{path} is missing"
    r = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
    assert r.returncode == 0, f"{path.name} syntax error:\n{r.stderr}"


def test_runbook_documents_the_mechanism():
    assert RUNBOOK.exists()
    text = RUNBOOK.read_text(encoding="utf-8")
    for token in ("/opt/modecissions/releases", "docker-compose.aws-images.gcp.yml", "dry-run", "Rollback"):
        assert token in text


def test_driver_never_targets_aws():
    # The AWS deploy surface must never be invoked (it may be named only in a
    # "must never run" warning comment, which documents the boundary).
    for path in (LOCAL, REMOTE):
        text = path.read_text(encoding="utf-8")
        assert "aws ssm" not in text
        assert "docker-compose.aws.yml" not in text
        assert "deploy_main_aws.py" not in text.replace(
            "scripts/deploy_main_aws.py is the", ""
        )  # allow the single boundary-warning reference, forbid any invocation


def test_remote_backs_up_both_dbs_before_mutation():
    text = REMOTE.read_text(encoding="utf-8")
    # Backup (step 1) precedes migrations (step 4) and the pull, textually.
    backup = text.index("step 1 backup")
    migrations = text.index("step 4 migrations")
    assert backup < migrations
    assert "modecissions" in text and "modecissions_gold" in text
    assert "pg_dump" in text and "SHA256SUMS" in text
    # An empty dump is refused (never a false safety net).
    assert "backup dump is empty" in text


def test_remote_is_fail_closed_with_verified_restore():
    text = REMOTE.read_text(encoding="utf-8")
    assert "trap on_err ERR EXIT" in text
    assert "rollback()" in text
    # Restore is checksum-verified AND atomic (all-or-nothing → no partial-drop
    # data loss), with competing backends terminated first.
    assert "restore_db" in text
    assert "want_sha" in text and "sha256_of" in text
    assert "--single-transaction" in text
    assert "pg_terminate_backend" in text
    assert "MUTATED" in text and "PROMOTED" in text


def test_remote_compose_runs_from_release_root_not_infra():
    """compose_up uses infra/-relative -f paths, so both callers must cd to the
    release ROOT, never into infra/ (the audit P0: cd .../infra -> infra/infra/)."""
    text = REMOTE.read_text(encoding="utf-8")
    assert 'cd "${RELEASE_DIR}/infra"' not in text
    assert 'cd "${PREV_TARGET}/infra"' not in text
    assert 'cd "${RELEASE_DIR}" && compose_up' in text
    assert 'cd "${PREV_TARGET}" && compose_up' in text


def test_dryrun_stops_before_any_db_or_service_mutation():
    text = REMOTE.read_text(encoding="utf-8")
    dry = text.index('DEPLOY_MODE:-apply}" == "dryrun"')
    migrations = text.index("step 4 migrations")
    up = text.index("step 5 deploy")
    # The dry-run exit sits before migrations and compose up.
    assert dry < migrations < up
    # MUTATED is only armed at/after migrations, so a dry-run leaves nothing to roll back.
    assert text.index("MUTATED=1") > dry


def test_remote_reuses_audited_safety_and_pins_by_tag():
    text = REMOTE.read_text(encoding="utf-8")
    assert "apply_db_migrations.sh" in text          # forward-only + drift guard
    assert "ghcr-auth-run.sh" in text                # server-owned pull
    assert "pull_policy: never" not in text          # remote pulls explicitly; overlay carries it
    local = LOCAL.read_text(encoding="utf-8")
    assert "pull_policy: never" in local             # the generated overlay pins it
    # Health gate is strict on version + app_env + readiness.
    assert "/healthz" in text and "/readyz" in text and "app_env" in text


def test_local_requires_provenance_and_explicit_target():
    text = LOCAL.read_text(encoding="utf-8")
    assert "^[0-9a-f]{40}$" in text                   # ref is a full commit SHA
    assert 'git rev-list -n 1 "${TARGET_TAG}"' in text  # tag must resolve to the ref
    for var in ("OMEGA_PROJECT_ID", "OMEGA_INSTANCE", "OMEGA_SOURCE_BUCKET", "OMEGA_GHCR_OWNER"):
        assert var in text
