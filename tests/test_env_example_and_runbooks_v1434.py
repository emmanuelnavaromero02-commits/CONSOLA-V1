"""Sprint v1.43.4 — Codex H3/H6 + Claude H5 + Claude M7.

Static guards for the documentation/config polish that v1.43.4
landed:

  * Runbook 01 now documents the two-phase Postgres-first arranque
    order so a slow-DB-init machine doesn't race the rest of the
    stack (Codex H3).
  * .gitignore excludes the ad-hoc ``infra/.env.backup*`` family so
    a stray ``git add -A`` after a secret rotation doesn't leak the
    rotation trail (Codex H6).
  * scripts/cleanup_env_backups.sh is executable, idempotent, and
    refuses to overwrite existing backups (Codex H6).
  * infra/.env.example documents BOOTSTRAP_ADMIN_EMAIL / _PASSWORD /
    _NAME so the runbook 02 first-boot flow works without
    silent failure (Claude H5).
  * infra/sync_dag_sources.py is gone — it was dead code with no
    callers (Claude M7).
"""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


# ── Codex H3: runbook 01 Postgres-first ───────────────────────────────────


def _runbook_01() -> str:
    return (REPO / "docs/runbook/01_arrancar_desde_cero.md").read_text(
        encoding="utf-8"
    )


def _runbook_02() -> str:
    return (REPO / "docs/runbook/02_primer_tenant.md").read_text(
        encoding="utf-8"
    )


def test_runbook_01_documents_postgres_first_arranque():
    """Runbook 01 must call out the two-phase order explicitly. Pre-
    v1.43.4 the doc said "up everything, sleep 90s" — which races
    on slow machines."""
    src = _runbook_01()
    # The phase headers introduced by Codex H3 fix.
    assert re.search(
        r"Paso\s*1\s*[-—]\s*Postgres primero", src, re.IGNORECASE
    ), "runbook 01 missing 'Paso 1 — Postgres primero' section"
    assert re.search(
        r"Paso\s*2\s*[-—]\s*Resto del stack", src, re.IGNORECASE
    ), "runbook 01 missing 'Paso 2 — Resto del stack' section"
    # The polling loop is the load-bearing detail.
    assert "State.Health.Status" in src, (
        "runbook 01 must show the docker inspect polling loop"
    )


def test_runbook_01_mentions_v1_43_4_rationale():
    """The reader needs to understand WHY the order matters; pure
    procedure without context invites copy-paste that drops the
    explanation when someone shortens the doc."""
    src = _runbook_01()
    assert "v1.43.4" in src
    assert "race" in src.lower() or "race-condition" in src.lower()


# ── Codex H6: .gitignore + cleanup script ─────────────────────────────────


def _gitignore() -> str:
    return (REPO / ".gitignore").read_text(encoding="utf-8")


def test_gitignore_excludes_env_backups():
    src = _gitignore()
    # Two flavours: the literal infra/.env.backup* path and a
    # **/.env.backup* glob for any nested copy.
    assert "infra/.env.backup" in src
    assert "infra/.env.backup-*" in src
    assert "**/.env.backup" in src


def test_no_env_backup_files_currently_tracked_by_git():
    """If someone already committed a backup before this hotfix
    landed, `git ls-files` would surface it. Catch the case where
    the .gitignore addition arrived too late."""
    import subprocess
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        # Not a git repo in this sandbox — skip rather than fail.
        return
    tracked = result.stdout.splitlines()
    offenders = [
        f for f in tracked
        if re.search(r"\.env\.backup(-.*)?$", f)
        or re.search(r"\.env\..*\.bak$", f)
    ]
    assert not offenders, (
        f"Git already tracks .env.backup files: {offenders}. "
        f"Remove with `git rm --cached <file>` before this hotfix lands."
    )


def test_cleanup_env_backups_script_exists_and_executable():
    script = REPO / "scripts/cleanup_env_backups.sh"
    assert script.exists(), "scripts/cleanup_env_backups.sh missing"
    mode = script.stat().st_mode
    assert mode & stat.S_IXUSR, (
        "scripts/cleanup_env_backups.sh must be executable (chmod +x)"
    )
    src = script.read_text(encoding="utf-8")
    # Defensive guards that justify reading this file:
    assert "set -euo pipefail" in src, (
        "cleanup_env_backups.sh must use strict bash mode"
    )
    assert "chmod 600" in src, (
        "moved backups must be locked down to owner-only read"
    )
    # Idempotency: the script must refuse to overwrite an existing dest.
    assert "already exists" in src, (
        "cleanup_env_backups.sh must refuse to overwrite existing backups"
    )


# ── Claude H5: BOOTSTRAP_ADMIN in .env.example ────────────────────────────


def _env_example() -> str:
    return (REPO / "infra/.env.example").read_text(encoding="utf-8")


def _bootstrap_sh() -> str:
    return (REPO / "infra/bootstrap.sh").read_text(encoding="utf-8")


def test_env_example_documents_bootstrap_admin_email():
    src = _env_example()
    assert re.search(
        r"^BOOTSTRAP_ADMIN_EMAIL=", src, re.MULTILINE
    ), "infra/.env.example missing BOOTSTRAP_ADMIN_EMAIL"


def test_env_example_documents_bootstrap_admin_password():
    src = _env_example()
    assert re.search(
        r"^BOOTSTRAP_ADMIN_PASSWORD=", src, re.MULTILINE
    ), "infra/.env.example missing BOOTSTRAP_ADMIN_PASSWORD"


def test_env_example_documents_bootstrap_admin_name():
    src = _env_example()
    assert re.search(
        r"^BOOTSTRAP_ADMIN_FULL_NAME=", src, re.MULTILINE
    ), "infra/.env.example missing BOOTSTRAP_ADMIN_FULL_NAME"
    assert "BOOTSTRAP_ADMIN_NAME=" not in src, (
        "infra/.env.example must match console/app/bootstrap_admin.py, "
        "which reads BOOTSTRAP_ADMIN_FULL_NAME"
    )


def test_env_example_documents_runtime_env_contract():
    src = _env_example()
    required = {
        "DATABASE_URL",
        "GOLD_DATABASE_URL",
        "MCP_INFRA_URL",
        "REFINEMENT_URL",
        "AIRFLOW_URL",
        "VAULT_URL",
        "REPLICON_URL",
        "HUBSPOT_URL",
        "SAP_HCM_URL",
        "SAP_S4HANA_URL",
        "SAP_SUCCESSFACTORS_URL",
        "INTERNAL_API_KEY",
        "INTERNAL_API_KEY_AIRFLOW_TO_CARTRIDGE",
        "CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK",
        "CONTROL_ROOM_ENABLE_EXTERNAL_DELIVERY",
        "RATE_LIMIT_ENABLED",
        "TRUSTED_PROXY_IPS",
        "BOOTSTRAP_ADMIN_EMAIL",
        "BOOTSTRAP_ADMIN_PASSWORD",
        "BOOTSTRAP_ADMIN_FULL_NAME",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "S3_BUCKET_NAME",
        "MINIO_ENDPOINT",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
        "MINIO_BUCKET",
        "MINIO_SECURE",
        "POSTGRES_PASSWORD",
        "OMEGA_CONSOLE_PASSWORD",
        "OMEGA_REFINEMENT_GOLD_PASSWORD",
        "OMEGA_MCP_INFRA_PASSWORD",
        "PG_HOST",
        "PG_PORT",
        "PG_DB",
        "PG_USER",
        "PG_PASSWORD",
    }
    missing = [
        name for name in sorted(required)
        if not re.search(rf"^{re.escape(name)}=", src, re.MULTILINE)
    ]
    assert not missing, f"infra/.env.example missing runtime keys: {missing}"


def test_bootstrap_sh_emits_runtime_env_contract():
    src = _bootstrap_sh()
    required = {
        "SUPERSET_SERVICE_PASSWORD",
        "DATABASE_URL",
        "GOLD_DATABASE_URL",
        "MCP_INFRA_URL",
        "REFINEMENT_URL",
        "AIRFLOW_URL",
        "VAULT_URL",
        "REPLICON_URL",
        "HUBSPOT_URL",
        "SAP_HCM_URL",
        "SAP_S4HANA_URL",
        "SAP_SUCCESSFACTORS_URL",
        "RATE_LIMIT_ENABLED",
        "TRUSTED_PROXY_IPS",
        "CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK",
        "CONTROL_ROOM_ENABLE_EXTERNAL_DELIVERY",
        "MINIO_ENDPOINT",
        "MINIO_ACCESS_KEY",
        "MINIO_BUCKET",
        "MINIO_SECURE",
        "PG_HOST",
        "PG_PASSWORD",
        "BOOTSTRAP_ADMIN_FULL_NAME",
    }
    missing = [name for name in sorted(required) if f"{name}=" not in src]
    assert not missing, f"infra/bootstrap.sh missing runtime keys: {missing}"


def test_bootstrap_fernet_generation_uses_stdlib_not_host_cryptography():
    src = _bootstrap_sh()
    assert "base64.urlsafe_b64encode(os.urandom(32))" in src
    assert "cryptography.fernet" not in src, (
        "bootstrap must not depend on host cryptography/cffi just to mint Fernet-shaped keys"
    )


def test_env_example_documents_superset_previous_secret_key():
    src = _env_example()
    assert re.search(r"^SUPERSET_PREVIOUS_SECRET_KEY=", src, re.MULTILINE), (
        "Superset rotations need SUPERSET_PREVIOUS_SECRET_KEY documented so "
        "superset re-encrypt-secrets can recover existing metadata"
    )


def test_local_superset_bootstrap_does_not_force_https_on_http_port():
    """Local compose exposes Superset over HTTP on :8088.

    If bootstrap emits HTTPS-forcing cookies/Talisman flags here, the UI
    redirects http://localhost:8088 to https://localhost:8088 even though
    the local container is not serving TLS. That breaks the E2E Superset
    reachability and Studio "Abrir Superset" checks.
    """
    for label, src in {
        "infra/.env.example": _env_example(),
        "infra/bootstrap.sh": _bootstrap_sh(),
    }.items():
        assert re.search(r"^SUPERSET_SESSION_COOKIE_SECURE=false$", src, re.MULTILINE), (
            f"{label} must keep local Superset cookies HTTP-compatible"
        )
        assert re.search(r"^SUPERSET_FORCE_HTTPS=false$", src, re.MULTILINE), (
            f"{label} must not force HTTPS for local http://localhost:8088"
        )


def test_env_example_bootstrap_admin_block_warns_against_committing():
    """The placeholder values in .env.example are deliberately weak.
    The surrounding comment must remind operators to rotate before
    boot AND to remove the variables after first login."""
    src = _env_example()
    block_match = re.search(
        r"#\s*===\s*Bootstrap admin.*?(?=#\s*===|\Z)",
        src,
        re.DOTALL | re.IGNORECASE,
    )
    assert block_match, "BOOTSTRAP admin section not found in .env.example"
    block = block_match.group(0)
    assert "NEVER commit" in block or "never commit" in block.lower()
    assert "first" in block.lower() and "boot" in block.lower()
    assert "reject" in block.lower(), (
        "Bootstrap password placeholder is public; .env.example must say "
        "the bootstrap CLI rejects it until replaced"
    )


def test_runbook_02_documents_manual_bootstrap_admin_cli():
    src = _runbook_02()
    assert "python -m app.bootstrap_admin" in src
    assert "BOOTSTRAP_ADMIN_PASSWORD" in src
    assert "BOOTSTRAP_ADMIN_FULL_NAME" in src
    assert "al primer boot" not in src.lower()


# ── Claude M7: dead code removed ──────────────────────────────────────────


def test_sync_dag_sources_dead_code_removed():
    """infra/sync_dag_sources.py was dead — no Makefile / workflow /
    runbook ever invoked it. Claude M7 flagged it; v1.43.4 deletes
    it."""
    legacy = REPO / "infra/sync_dag_sources.py"
    assert not legacy.exists(), (
        f"{legacy} should have been removed in v1.43.4 (Claude M7)"
    )
