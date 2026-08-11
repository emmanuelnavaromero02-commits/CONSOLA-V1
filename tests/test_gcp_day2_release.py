from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
GCP_SHELL_DIR = REPO / "scripts" / "gcp"
GCP_COMPOSE_DIR = REPO / "infra" / "terraform-gcp" / "deploy"


def _read(relative: str) -> str:
    return (REPO / relative).read_text(encoding="utf-8")


def _load_module():
    path = REPO / "scripts/gcp_release.py"
    spec = importlib.util.spec_from_file_location("gcp_release", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_gcp_release_overlay_covers_exactly_15_proprietary_images() -> None:
    overlay = _read("infra/terraform-gcp/deploy/docker-compose.release.yml")
    repositories = re.findall(
        r"image: ghcr\.io/\$\{GHCR_OWNER[^}]*\}/([a-z0-9_-]+):\$\{IMAGE_TAG",
        overlay,
    )
    assert len(set(repositories)) == 15
    assert set(repositories) == {
        "console",
        "workspace",
        "refinement",
        "vault",
        "mcp-infra",
        "airflow",
        "replicon",
        "hubspot",
        "salesforce",
        "banxico",
        "inegi",
        "sec_edgar",
        "sap_hcm",
        "sap_successfactors",
        "sap_s4hana",
    }
    assert "latest" not in overlay
    assert overlay.count("/airflow:${IMAGE_TAG") == 3


def test_gcp_release_overlay_resolves_with_base_compose() -> None:
    command = [
        "docker",
        "compose",
        "--env-file",
        str(REPO / "infra/.env.example"),
        "-f",
        str(REPO / "infra/docker-compose.yml"),
        "-f",
        str(GCP_COMPOSE_DIR / "docker-compose.release.yml"),
        "--profile",
        "sap",
        "config",
        "--images",
    ]
    env = os.environ.copy()
    env.update({"IMAGE_TAG": "v1.45.207-beta", "GHCR_OWNER": "example-owner"})
    result = subprocess.run(command, text=True, capture_output=True, env=env, check=False)
    assert result.returncode == 0, result.stderr
    ghcr = {
        line
        for line in result.stdout.splitlines()
        if line.startswith("ghcr.io/example-owner/")
    }
    assert len(ghcr) == 15
    assert all(line.endswith(":v1.45.207-beta") for line in ghcr)


@pytest.mark.parametrize(
    "script",
    ["day2-release.sh", "backup.sh", "restore-rehearsal.sh"],
)
def test_gcp_day2_shell_is_syntax_valid(script: str) -> None:
    result = subprocess.run(
        ["bash", "-n", str(GCP_SHELL_DIR / script)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_deploy_fails_closed_and_promotes_only_after_all_gates() -> None:
    deploy = _read("scripts/gcp/day2-release.sh")
    assert "ifGenerationMatch" not in deploy  # artifact upload is operator-side
    assert "artifact checksum" in deploy
    assert "backup source commit does not match current release" in deploy
    assert "release does not contain the audited helper" in deploy
    assert "registry credential found in runtime env" in deploy
    assert "atomically refreshed from current release" in deploy
    assert 'OMEGA_GCP_GHCR_AUTH_RUNNER' in deploy
    assert 'OMEGA_GHCR_PULL_SECRET_VERSION="$GHCR_SECRET_VERSION"' in deploy
    assert "pull.log" not in deploy
    assert "15/15 tag=${TARGET_TAG}" in deploy
    assert "@sha256:[0-9a-f]{64}" in deploy
    assert "--no-build --no-deps --force-recreate postgres postgres_gold" in deploy
    assert "named volume identity changed" in deploy
    assert "scripts/apply_db_migrations.sh" in deploy
    assert "/readyz?require_data=1" in deploy
    assert "exactly one scheduler" in deploy
    assert "writers and scheduler remain stopped; current was not promoted" in deploy
    assert "down -v" not in deploy
    assert "docker compose down" not in deploy

    pull = deploy.index("private GHCR release pull")
    fence = deploy.index('"${COMPOSE[@]}" stop --timeout 60')
    migrate = deploy.index("scripts/apply_db_migrations.sh")
    readiness = deploy.index("candidate data readiness")
    scheduler = deploy.index("canonical scheduler cardinality")
    promotion = deploy.index("atomic current promotion")
    assert pull < fence < migrate < readiness < scheduler < promotion


def test_backup_is_writer_fenced_versioned_and_immutable() -> None:
    backup = _read("scripts/gcp/backup.sh")
    assert "expected one scheduler" in backup
    assert "registry credential found in runtime env" in backup
    assert "all known application writers stopped" in backup
    assert "pg_dumpall" in backup
    assert 'get("versioning", {}).get("enabled") is not True' in backup
    assert "object-generation" not in backup
    assert "A second generation listing" in backup
    assert "ifGenerationMatch=0" in backup
    assert '"generation": str(item["generation"])' in backup
    assert '"crc32c": item.get("crc32c")' in backup
    assert '"md5": item.get("md5Hash")' in backup
    assert '"plaintext_runtime_secrets_included": False' in backup
    assert '"database_contents_sensitive": True' in backup
    assert "runtime restored after backup" in backup
    assert "down -v" not in backup


def test_restore_rehearsal_isolated_from_live_volumes() -> None:
    rehearsal = _read("scripts/gcp/restore-rehearsal.sh")
    assert "omega_gcp_rehearsal_" in rehearsal
    assert "--network none" in rehearsal
    assert "verified_generations" in rehearsal
    assert "ThreadPoolExecutor" in rehearsal
    assert "schema_migrations" in rehearsal
    assert "live_volumes_untouched=true" in rehearsal
    assert "mode_postgres" not in rehearsal
    assert "mode_postgres_gold" not in rehearsal
    assert "docker compose" not in rehearsal


def test_migration_runner_accepts_only_a_safe_explicit_project_and_env() -> None:
    migration = _read("scripts/apply_db_migrations.sh")
    assert "OMEGA_MIGRATION_ENV_FILE" in migration
    assert "OMEGA_MIGRATION_COMPOSE_PROJECT_NAME" in migration
    assert "^[a-z0-9][a-z0-9_-]*$" in migration
    assert 'docker compose --project-name "${COMPOSE_PROJECT_NAME_VALUE}"' in migration


def test_startup_publishes_shared_runtime_only_after_gcp_reconciliation() -> None:
    startup = _read("infra/terraform-gcp/templates/startup.sh.tftpl")
    shared_copy = startup.index('cp infra/.env "$${SHARED_ENV}"')
    assert shared_copy > startup.index("set_env S3_ENDPOINT_URL")
    assert shared_copy > startup.index("if hmac_secret_key=")
    assert (
        'cp infra/docker-compose.gcp.yml "$${APP_ROOT}/shared/docker-compose.gcp.yml"'
        in startup
    )


def test_operator_controller_validates_identity_and_redacts_evidence(monkeypatch) -> None:
    module = _load_module()
    assert module.redact("password=supersecret") == "[REDACTED]"
    assert "[REDACTED]" in module.redact("token: ghp_examplevalue")
    assert module.TAG_RE.fullmatch("v1.45.207-beta")
    assert not module.TAG_RE.fullmatch("latest")

    git_values = {
        ("cat-file", "-t", "refs/tags/v1.45.207-beta"): "tag",
        ("rev-parse", "refs/tags/v1.45.207-beta^{commit}"): "a" * 40,
        ("rev-parse", "origin/main"): "a" * 40,
        ("show", f"{'a' * 40}:VERSION"): "1.45.207-beta",
    }
    monkeypatch.setattr(module, "git", lambda *args, **_kwargs: git_values[args])
    monkeypatch.setattr(
        module,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, f"{'a' * 40}\trefs/tags/v1.45.207-beta^{{}}\n", ""
        ),
    )
    assert (
        module.validate_release_identity("v1.45.207-beta", "a" * 40)
        == "1.45.207-beta"
    )


def test_remote_evidence_parser_rejects_missing_or_failed_gates(tmp_path: Path) -> None:
    module = _load_module()
    stdout = (
        "OMEGA_GCP_RELEASE_CHECK\tartifact\tPASS\tsha256=abc\n"
        'OMEGA_GCP_RELEASE_JSON={"status":"PASS","deploy_ref":"abc"}\n'
    )
    checks, payload = module.parse_remote(
        stdout, "OMEGA_GCP_RELEASE_CHECK", "OMEGA_GCP_RELEASE_JSON"
    )
    assert checks == [{"name": "artifact", "status": "PASS", "evidence": "sha256=abc"}]
    result = module.RemoteResult(0, stdout, "")
    assert (
        module.write_evidence(
            tmp_path,
            operation="Deploy",
            result=result,
            checks=checks,
            payload=payload,
        )
        == "PASS"
    )
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["secrets_included"] is False
    assert summary["status"] == "PASS"


def test_make_targets_and_runbook_define_no_blind_n_minus_one_rollback() -> None:
    makefile = _read("Makefile")
    runbook = _read("docs/runbook/16_gcp_canonical_day2_release.md")
    assert "backup-gcp-canonical:" in makefile
    assert "deploy-gcp-canonical:" in makefile
    assert "restore-rehearsal-gcp:" in makefile
    assert "There is intentionally no blind N-1" in runbook
    assert "AWS is DR/standby, not a second writer" in runbook
    assert "GCP_OBJECT_VERIFY_MODE=all" in runbook
    assert "/opt/modecissions/shared/bin/ghcr-auth-run" in runbook
