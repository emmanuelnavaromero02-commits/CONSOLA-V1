from __future__ import annotations

import os
import re
import stat
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def _gitignore() -> str:
    return (REPO / ".gitignore").read_text(encoding="utf-8")


def test_gitignore_excludes_env_backups():
    src = _gitignore()
    assert "infra/.env.backup" in src
    assert "infra/.env.backup-*" in src
    assert "**/.env.backup" in src


def test_no_env_backup_files_currently_tracked_by_git():
    import subprocess
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
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
    assert "set -euo pipefail" in src, (
        "cleanup_env_backups.sh must use strict bash mode"
    )
    assert "chmod 600" in src, (
        "moved backups must be locked down to owner-only read"
    )
    assert "already exists" in src, (
        "cleanup_env_backups.sh must refuse to overwrite existing backups"
    )


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
        "SALESFORCE_URL",
        "SAP_HCM_URL",
        "SAP_S4HANA_URL",
        "SAP_SUCCESSFACTORS_URL",
        "SAP_B1_URL",
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
        "AIRFLOW_FERNET_KEY",
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
        "SALESFORCE_URL",
        "SAP_HCM_URL",
        "SAP_S4HANA_URL",
        "SAP_SUCCESSFACTORS_URL",
        "SAP_B1_URL",
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


def test_bootstrap_generates_airflow_fernet_key_with_the_stdlib_script():
    src = _bootstrap_sh()
    assert (
        'AIRFLOW_FERNET_KEY="$("${BOOTSTRAP_PYTHON}" -c "${FERNET_KEY_SCRIPT}")"'
        in src
    )
    heredoc = src.split('cat > "${ENV_FILE}" <<EOF', 1)[1].split("\nEOF", 1)[0]
    assert re.search(
        r"^AIRFLOW_FERNET_KEY=\$\{AIRFLOW_FERNET_KEY\}$", heredoc, re.MULTILINE
    )


def test_bootstrap_heredoc_does_not_execute_comment_backticks():
    src = _bootstrap_sh()
    heredoc = src.split('cat > "${ENV_FILE}" <<EOF', 1)[1].split("\nEOF", 1)[0]
    assert "`" not in heredoc.replace("\\`", ""), (
        "infra/bootstrap.sh heredoc comments must not contain raw backticks; "
        "bash executes them while generating infra/.env"
    )
    assert "${ADMIN_EMAIL}" not in heredoc


def test_env_example_documents_superset_previous_secret_key():
    src = _env_example()
    assert re.search(r"^SUPERSET_PREVIOUS_SECRET_KEY=", src, re.MULTILINE), (
        "Superset rotations need SUPERSET_PREVIOUS_SECRET_KEY documented so "
        "superset re-encrypt-secrets can recover existing metadata"
    )


def test_local_superset_bootstrap_does_not_force_https_on_http_port():
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


def test_sync_dag_sources_dead_code_removed():
    legacy = REPO / "infra/sync_dag_sources.py"
    assert not legacy.exists(), (
        f"{legacy} should have been removed in v1.43.4"
    )
