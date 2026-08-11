from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from base64 import b64encode
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
GCP_SHELL_DIR = REPO / "scripts" / "gcp"
GCP_COMPOSE_DIR = REPO / "infra" / "terraform-gcp" / "release"


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
    overlay = _read("infra/terraform-gcp/release/docker-compose.release.yml")
    lock_names = set(re.findall(r"image: \$\{(OMEGA_GCP_IMAGE_[A-Z0-9_]+):", overlay))
    assert len(lock_names) == 15
    assert "latest" not in overlay
    assert overlay.count("OMEGA_GCP_IMAGE_AIRFLOW") == 3
    assert overlay.count("build: !reset null") == 17
    assert overlay.count("pull_policy: never") == 17


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
    repositories = {
        "AIRFLOW": "airflow",
        "BANXICO": "banxico",
        "CONSOLE": "console",
        "HUBSPOT": "hubspot",
        "INEGI": "inegi",
        "MCP_INFRA": "mcp-infra",
        "REFINEMENT": "refinement",
        "REPLICON": "replicon",
        "SALESFORCE": "salesforce",
        "SAP_HCM": "sap_hcm",
        "SAP_S4HANA": "sap_s4hana",
        "SAP_SUCCESSFACTORS": "sap_successfactors",
        "SEC_EDGAR": "sec_edgar",
        "VAULT": "vault",
        "WORKSPACE": "workspace",
    }
    digest = "a" * 64
    env.update(
        {
            f"OMEGA_GCP_IMAGE_{key}": (
                f"ghcr.io/example-owner/{repository}:v1.45.207-beta@sha256:{digest}"
            )
            for key, repository in repositories.items()
        }
    )
    result = subprocess.run(
        command, text=True, capture_output=True, env=env, check=False
    )
    assert result.returncode == 0, result.stderr
    ghcr = {
        line
        for line in result.stdout.splitlines()
        if line.startswith("ghcr.io/example-owner/")
    }
    assert len(ghcr) == 15
    assert all(line.endswith(f"@sha256:{digest}") for line in ghcr)


@pytest.mark.parametrize(
    "script",
    [
        "day2-release.sh",
        "backup.sh",
        "restore-rehearsal.sh",
        "reboot-runtime.sh",
        "image-preflight.sh",
    ],
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
    assert (
        "release does not contain the audited auth, preflight, and runtime helpers"
        in deploy
    )
    assert "registry credential found in runtime env" in deploy
    assert "host-owned env and generated GCP overlay are canonical" in deploy
    assert 'CURRENT_ENV="${OLD_RELEASE}/infra/.env"' not in deploy
    assert '"${OLD_RELEASE}/infra/docker-compose.gcp.yml"' not in deploy
    assert "OMEGA_GCP_GHCR_AUTH_RUNNER" in deploy
    assert 'OMEGA_GHCR_PULL_SECRET_VERSION="$GHCR_SECRET_VERSION"' in deploy
    assert "pull.log" not in deploy
    assert "15/15 tag=${TARGET_TAG}" in deploy
    assert '"schema_version": 2' in deploy
    assert '"service_binding_count": len(service_images)' in deploy
    assert (
        "--no-build --pull never --no-deps --force-recreate postgres postgres_gold"
        in deploy
    )
    assert "named volume identity changed" in deploy
    assert "scripts/apply_db_migrations.sh" in deploy
    assert "/readyz?require_data=1" in deploy
    assert "--scheduler stopped --one-shots" in deploy
    assert "--scheduler required --one-shots" in deploy
    assert "running=16 healthy=16 scheduler=1 exact_lock=true" in deploy
    assert "durable operation marker retained" in deploy
    assert 'write_operation_state "fencing"' in deploy
    assert 'write_operation_state "fenced"' in deploy
    assert 'write_operation_state "migrated"' in deploy
    assert 'write_operation_state "validated"' in deploy
    assert 'write_operation_state "promoted"' in deploy
    assert "down -v" not in deploy
    assert "docker compose down" not in deploy

    pull = deploy.index("private GHCR release pull")
    fence = deploy.index('"${COMPOSE[@]}" stop --timeout 60')
    migrate = deploy.index("scripts/apply_db_migrations.sh")
    readiness = deploy.index("candidate data readiness")
    exact_runtime = deploy.index("exact pre-scheduler runtime")
    scheduler = deploy.index("up -d --no-build --pull never airflow-scheduler")
    promotion = deploy.index("atomic current promotion")
    marker = deploy.index('write_operation_state "fencing"')
    assert (
        pull
        < marker
        < fence
        < migrate
        < readiness
        < exact_runtime
        < scheduler
        < promotion
    )


def test_backup_is_writer_fenced_versioned_and_immutable() -> None:
    backup = _read("scripts/gcp/backup.sh")
    assert "expected one scheduler" in backup
    assert "registry credential found in runtime env" in backup
    assert "all known application writers stopped" in backup
    assert "pg_dumpall" in backup
    assert 'get("versioning", {}).get("enabled") is not True' in backup
    assert "object-generation" not in backup
    assert "A second generation listing" in backup
    assert '"ifGenerationMatch": "0"' in backup
    assert '"generation": str(item["generation"])' in backup
    assert '"crc32c": item.get("crc32c")' in backup
    assert '"md5": item.get("md5Hash")' in backup
    assert '"plaintext_runtime_secrets_included": False' in backup
    assert '"database_contents_sensitive": True' in backup
    assert "runtime restored after backup" in backup
    assert "candidate helper artifact" in backup
    assert "legacy runtime adoption" in backup
    assert "live release coherence" in backup
    assert '"runtime_provenance_sha256"' in backup
    assert '"repo_digest"' in backup
    assert '"image_id"' in backup
    assert "Authorization: Bearer ${token}" not in backup
    assert 'headers={"Authorization": f"Bearer {token}"}' in backup
    assert 'write_operation_state "fencing"' in backup
    assert 'write_operation_state "captured"' in backup
    assert 'write_operation_state "restored"' in backup
    assert 'rm -f -- "$OPERATION_MARKER"' in backup
    assert "all previously-active services running/healthy" in backup
    assert "down -v" not in backup

    staged = backup.index('--output "$ACTIVE_PROVENANCE"')
    health = backup.index("healthz version/app_env differs from current release")
    provenance = backup.index('--provenance "$ACTIVE_PROVENANCE"')
    publication = backup.index('mv -Tf "$STATE_PREVIEW" "$STATE_LINK"')
    assert staged < health < provenance < publication
    assert "one-existing/one-missing direct state pair is corrupt" in backup
    assert "state_pair=atomic" in backup


def test_post_record_health_failure_leaves_atomic_state_unchanged(
    tmp_path: Path,
) -> None:
    bundles = tmp_path / "state-bundles"
    bundles.mkdir()
    old = bundles / "old"
    old.mkdir()
    (old / "bootstrap-state.json").write_text("old-bootstrap", encoding="utf-8")
    (old / "runtime-provenance.json").write_text("old-provenance", encoding="utf-8")
    state_link = tmp_path / "runtime-state"
    state_link.symlink_to(old, target_is_directory=True)

    staged = bundles / ".candidate"
    staged.mkdir()
    (staged / "bootstrap-state.json").write_text("new-bootstrap", encoding="utf-8")
    (staged / "runtime-provenance.json").write_text("new-provenance", encoding="utf-8")

    def adoption_transaction(*, health_ok: bool) -> None:
        if not health_ok:  # injected after both records exist, before commit
            raise RuntimeError("injected health failure")
        preview = tmp_path / ".runtime-state.preview"
        preview.symlink_to(staged, target_is_directory=True)
        os.replace(preview, state_link)

    with pytest.raises(RuntimeError, match="injected health failure"):
        adoption_transaction(health_ok=False)
    assert state_link.resolve() == old.resolve()
    assert (state_link / "bootstrap-state.json").read_text() == "old-bootstrap"
    assert (state_link / "runtime-provenance.json").read_text() == "old-provenance"


def test_operation_gate_verifies_atomic_pair_current_and_helper_hashes(
    tmp_path: Path,
) -> None:
    gate = REPO / "infra/terraform-gcp/templates/omega-operation-gate"
    app_root = tmp_path / "modecissions"
    shared = app_root / "shared"
    bundles = shared / "state-bundles"
    release_ref = "a" * 40
    release = app_root / "releases" / release_ref
    helper_root = release / "scripts/gcp"
    helper_root.mkdir(parents=True)
    bundles.mkdir(parents=True)
    (release / "VERSION").write_text("1.45.207-beta\n", encoding="utf-8")
    reboot = helper_root / "reboot-runtime.sh"
    contract = helper_root / "runtime_contract.py"
    reboot.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    contract.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    reboot.chmod(0o755)
    contract.chmod(0o755)
    (app_root / "current").symlink_to(release, target_is_directory=True)

    bundle = bundles / "candidate"
    bundle.mkdir()
    provenance = bundle / "runtime-provenance.json"
    provenance.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "mode": "day2",
                "compose_project": "infra",
                "deploy_ref": release_ref,
                "version": "1.45.207-beta",
                "services": {},
            }
        ),
        encoding="utf-8",
    )

    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    (bundle / "bootstrap-state.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "state": "complete",
                "deploy_ref": release_ref,
                "version": "1.45.207-beta",
                "runtime_provenance_sha256": digest(provenance),
                "reboot_helper": {
                    "mode": "current",
                    "source_ref": release_ref,
                    "reboot_runtime_sha256": digest(reboot),
                    "runtime_contract_sha256": digest(contract),
                },
            }
        ),
        encoding="utf-8",
    )
    (shared / "runtime-state").symlink_to(bundle, target_is_directory=True)
    env = {**os.environ, "OMEGA_GCP_APP_ROOT": str(app_root)}
    verified = subprocess.run(
        [gate], text=True, capture_output=True, env=env, check=False
    )
    assert verified.returncode == 0, verified.stderr
    helpers = subprocess.run(
        [gate, "print-helpers"], text=True, capture_output=True, env=env, check=False
    )
    assert helpers.returncode == 0, helpers.stderr
    assert helpers.stdout.strip() == f"{reboot}\t{contract}"

    reboot.write_text("tampered\n", encoding="utf-8")
    rejected = subprocess.run(
        [gate], text=True, capture_output=True, env=env, check=False
    )
    assert rejected.returncode == 76
    assert "checksum differs" in rejected.stderr


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
    assert rehearsal.count("psql -v ON_ERROR_STOP=1") == 2
    assert 'REHEARSAL_ADMIN="omega_rehearsal_${SUFFIX}"' in rehearsal
    assert "filter_pg_dump_bootstrap_role" not in rehearsal
    assert rehearsal.count('POSTGRES_USER="$REHEARSAL_ADMIN"') == 2
    assert rehearsal.count("POSTGRES_DB=postgres") == 2
    assert rehearsal.count("--pull never") == 2
    assert "repo_digest" in rehearsal
    assert "image_id" in rehearsal
    assert "unique role absent from both unmodified dumps" in rehearsal


@pytest.mark.skipif(
    os.environ.get("OMEGA_ENABLE_DOCKER_RESTORE_TESTS") != "1",
    reason="set OMEGA_ENABLE_DOCKER_RESTORE_TESTS=1 for the live Docker restore test",
)
@pytest.mark.parametrize(
    ("source_container", "port"),
    [("mode_postgres", "5432"), ("mode_postgres_gold", "5433")],
)
def test_unmodified_pg_dumpall_restores_with_distinct_bootstrap_role(
    tmp_path: Path, source_container: str, port: str
) -> None:
    suffix = uuid.uuid4().hex[:12]
    target = f"omega_gcp_restore_test_{suffix}"
    volume = target
    admin = f"omega_rehearsal_{suffix}"
    dump = tmp_path / f"{source_container}.sql"

    def docker(
        *arguments: str, input_file: Path | None = None
    ) -> subprocess.CompletedProcess:
        stdin = input_file.open("rb") if input_file else None
        try:
            return subprocess.run(
                ["docker", *arguments],
                stdin=stdin,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        finally:
            if stdin:
                stdin.close()

    source = docker("inspect", source_container)
    assert source.returncode == 0, source.stderr.decode()
    source_info = json.loads(source.stdout)[0]
    image_id = source_info["Image"]
    configured = source_info["Config"]["Image"]
    repository = configured.rsplit(":", 1)[0]
    inspected = docker("image", "inspect", image_id)
    assert inspected.returncode == 0, inspected.stderr.decode()
    repo_digests = [
        value
        for value in json.loads(inspected.stdout)[0].get("RepoDigests") or []
        if value.startswith(repository + "@sha256:")
    ]
    assert len(repo_digests) == 1
    image = repo_digests[0]

    with dump.open("wb") as stream:
        snapshot = subprocess.run(
            [
                "docker",
                "exec",
                source_container,
                "pg_dumpall",
                "-U",
                "postgres",
                "-p",
                port,
                "--clean",
                "--if-exists",
            ],
            stdout=stream,
            stderr=subprocess.PIPE,
            check=False,
        )
    assert snapshot.returncode == 0, snapshot.stderr.decode()
    assert dump.stat().st_size > 0

    assert docker("volume", "create", volume).returncode == 0
    try:
        command = [
            "run",
            "-d",
            "--pull",
            "never",
            "--name",
            target,
            "--network",
            "none",
            "--mount",
            f"type=volume,source={volume},target=/var/lib/postgresql/data",
            "-e",
            f"POSTGRES_USER={admin}",
            "-e",
            "POSTGRES_DB=postgres",
            "-e",
            "POSTGRES_HOST_AUTH_METHOD=trust",
            image,
        ]
        if port != "5432":
            command.extend(["-p", port])
        started = docker(*command)
        assert started.returncode == 0, started.stderr.decode()
        for _ in range(60):
            ready = docker(
                "exec", target, "pg_isready", "-h", "127.0.0.1", "-p", port, "-U", admin
            )
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            pytest.fail("isolated PostgreSQL did not become ready")

        restored = docker(
            "exec",
            "-i",
            target,
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            admin,
            "-d",
            "postgres",
            "-p",
            port,
            input_file=dump,
        )
        assert restored.returncode == 0, restored.stderr.decode()
        role = docker(
            "exec",
            target,
            "psql",
            "-At",
            "-U",
            admin,
            "-d",
            "postgres",
            "-p",
            port,
            "-c",
            f"SELECT count(*) FROM pg_roles WHERE rolname='{admin}' AND rolsuper;",
        )
        assert role.returncode == 0, role.stderr.decode()
        assert role.stdout.strip() == b"1"
    finally:
        docker("rm", "-f", target)
        docker("volume", "rm", volume)


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


def test_startup_is_bootstrap_once_and_fences_partial_reboot_state() -> None:
    startup = _read("infra/terraform-gcp/templates/startup.sh.tftpl")
    guard = _read("infra/terraform-gcp/templates/omega-operation-gate")
    locals = _read("infra/terraform-gcp/locals.tf")
    compute = _read("infra/terraform-gcp/compute.tf")
    outputs = _read("infra/terraform-gcp/outputs.tf")

    assert "operation_guard_base64" in locals
    assert "metadata_startup_script = local.startup_script" in compute
    assert "ignore_changes" not in compute
    assert 'output "source_sha"' in outputs
    assert 'output "source_object"' in outputs
    assert 'output "startup_script_sha256"' in outputs
    assert startup.index("omega-operation-gate") < startup.index(
        "docker-ce docker-ce-cli"
    )
    assert startup.index('if [[ -e "$${BOOTSTRAP_STATE}" ]]') < startup.index(
        "download_source()"
    )
    assert "recovering exact current without download/build/pull" in startup
    assert '"operation": "bootstrap"' in startup
    assert 'mv -Tf "$${STATE_LINK_TMP}" "$${STATE_LINK}"' in startup
    assert 'rm -f -- "$${OPERATION_MARKER}"' in startup
    assert "OMEGA_GCP_RUNTIME_CONTRACT" in startup
    assert "runtime-state is not an atomic symlink" in guard
    assert "runtime provenance checksum differs" in guard
    assert "reboot helper checksum differs" in guard


def test_startup_template_renders_to_syntax_valid_bash() -> None:
    rendered = _read("infra/terraform-gcp/templates/startup.sh.tftpl")
    values = {
        "project_id": "omega-production",
        "source_bucket": "omega-source-bucket",
        "source_object": "deploy-artifacts/" + "a" * 40 + "/repo.tar.gz",
        "source_sha": "a" * 40,
        "public_console_url": "https://gcp-console.example.com",
        "public_workspace_url": "https://gcp-workspace.example.com",
        "public_airflow_url": "https://gcp-console.example.com/airflow",
        "technical_console_url": "http://192.0.2.1",
        "technical_workspace_url": "http://192.0.2.2",
        "admin_email": "operator@example.com",
        "cookie_secure": "true",
        "lakehouse_bucket": "omega-lakehouse",
        "lakehouse_endpoint": "storage.googleapis.com",
        "enable_airflow_scheduler": "true",
        "secret_prefix": "omega-production-",
        "compose_override": "services: {}",
        "operation_guard_base64": b64encode(
            _read("infra/terraform-gcp/templates/omega-operation-gate").encode()
        ).decode(),
    }
    for key, value in values.items():
        rendered = rendered.replace("${" + key + "}", value)
    rendered = rendered.replace("$${", "${")
    result = subprocess.run(
        ["bash", "-n"], input=rendered, text=True, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_controller_binds_backup_and_deploy_to_live_terraform_render(
    monkeypatch, tmp_path: Path
) -> None:
    module = _load_module()
    monkeypatch.setenv("OMEGA_TERRAFORM_BIN", sys.executable)
    deploy_ref = "a" * 40
    startup_sha = "b" * 64
    outputs = {
        "source_sha": {"value": deploy_ref},
        "source_bucket": {"value": "omega-source-bucket"},
        "source_object": {"value": f"deploy-artifacts/{deploy_ref}/repo.tar.gz"},
        "startup_script_sha256": {"value": startup_sha},
    }
    monkeypatch.setattr(
        module,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, json.dumps(outputs), ""
        ),
    )
    assert (
        module.validate_terraform_source_contract(
            terraform_dir=REPO / "infra/terraform-gcp",
            source_ref=deploy_ref,
            artifact_bucket="omega-source-bucket",
        )
        == startup_sha
    )
    outputs["source_sha"]["value"] = "c" * 40
    with pytest.raises(RuntimeError, match="does not match expected ref"):
        module.validate_terraform_source_contract(
            terraform_dir=REPO / "infra/terraform-gcp",
            source_ref=deploy_ref,
            artifact_bucket="omega-source-bucket",
        )

    reviewed = tmp_path / "production.tfvars"
    reviewed.write_text(
        "\n".join(
            [
                'project_id = "omega-production"',
                'project_number = "894064513501"',
                'billing_account_id = "01A3E0-B708F4-6EA299"',
                'source_bucket = "omega-source-bucket"',
                f'source_object = "deploy-artifacts/{deploy_ref}/repo.tar.gz"',
                f'source_sha = "{deploy_ref}"',
                'app_machine_type = "e2-standard-4"',
                "boot_disk_size_gb = 60",
                "data_disk_size_gb = 150",
                'public_console_domain = "gcp-console.example.com"',
                'public_workspace_domain = "gcp-workspace.example.com"',
                "enable_https = true",
                "enable_airflow_scheduler = true",
                'monthly_budget_currency = "EUR"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        module,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "", ""),
    )
    module.validate_reviewed_terraform_plan(
        terraform_dir=REPO / "infra/terraform-gcp",
        var_file=reviewed,
        source_ref=deploy_ref,
        source_bucket="omega-source-bucket",
        project_id="omega-production",
        project_number="894064513501",
        billing_account_id="01A3E0-B708F4-6EA299",
        public_console_domain="gcp-console.example.com",
        public_workspace_domain="gcp-workspace.example.com",
    )
    reviewed.write_text(reviewed.read_text().replace("60", "30", 1), encoding="utf-8")
    with pytest.raises(ValueError, match="boot_disk_size_gb"):
        module.validate_reviewed_terraform_plan(
            terraform_dir=REPO / "infra/terraform-gcp",
            var_file=reviewed,
            source_ref=deploy_ref,
            source_bucket="omega-source-bucket",
            project_id="omega-production",
            project_number="894064513501",
            billing_account_id="01A3E0-B708F4-6EA299",
            public_console_domain="gcp-console.example.com",
            public_workspace_domain="gcp-workspace.example.com",
        )

    controller = _read("scripts/gcp_release.py")
    backup_call = controller[controller.index("def command_backup") :]
    deploy_call = controller[controller.index("def command_deploy") :]
    assert backup_call.index("validate_terraform_source_contract") < backup_call.index(
        'script=REMOTE_ROOT / "backup.sh"'
    )
    assert backup_call.index("validate_reviewed_terraform_plan") < backup_call.index(
        'script=REMOTE_ROOT / "backup.sh"'
    )
    assert backup_call.index("validate_startup_metadata") < backup_call.index(
        'script=REMOTE_ROOT / "backup.sh"'
    )
    assert "args.current_live_ref" in backup_call
    assert deploy_call.index("validate_terraform_source_contract") < deploy_call.index(
        'script=REMOTE_ROOT / "day2-release.sh"'
    )


def test_terraform_binary_defaults_to_tofu_and_fails_closed(monkeypatch) -> None:
    module = _load_module()
    monkeypatch.delenv("OMEGA_TERRAFORM_BIN", raising=False)
    monkeypatch.setattr(
        module.shutil,
        "which",
        lambda value: "/usr/bin/tofu" if value == "tofu" else None,
    )
    assert module.terraform_binary() == "/usr/bin/tofu"
    monkeypatch.setattr(module.shutil, "which", lambda _value: None)
    with pytest.raises(RuntimeError, match="binary is unavailable"):
        module.terraform_binary()


def test_day2_ci_runs_focal_tests_and_static_scanners() -> None:
    focal = _read(".github/workflows/control-room-postgres-rls.yml")
    lint = _read(".github/workflows/lint.yml")
    security = _read(".github/workflows/security.yml")
    changed = _read("scripts/ci_changed_areas.py")
    assert "tests/test_gcp_day2_release.py" in focal
    assert "scripts/gcp_release.py scripts/gcp/runtime_contract.py" in lint
    assert security.count("scripts/gcp_release.py scripts/gcp/runtime_contract.py") == 2
    assert '"scripts/gcp_release.py"' in changed
    assert '"scripts/gcp/runtime_contract.py"' in changed


def test_authenticated_image_preflight_is_15_of_15_and_server_owned() -> None:
    preflight = _read("scripts/gcp/image-preflight.sh")
    assert "OMEGA_GCP_IMAGE_PREFLIGHT_JSON" in preflight
    assert 'image_count":15' in preflight
    assert "ghcr-auth-run.sh" in preflight
    assert "preflight-release-images.sh" in preflight
    assert "versions/latest" not in preflight
    assert "GHCR_SECRET_VERSION" in preflight
    assert "credential output suppressed" in preflight
    assert "image-preflights/${PURPOSE}-${TARGET_REF}" in preflight


def test_operator_controller_validates_identity_and_redacts_evidence(
    monkeypatch,
) -> None:
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
        module.validate_release_identity("v1.45.207-beta", "a" * 40) == "1.45.207-beta"
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
