from __future__ import annotations

import base64
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
HELPER = REPO / "infra/terraform-gcp/release/hydrate-runtime-secrets.sh"
STARTUP = REPO / "infra/terraform-gcp/templates/startup.sh.tftpl"
CANONICAL_REMOTE = REPO / "scripts/gcp/gcp-canonical-deploy-remote.sh"
GCP_COMPOSE = REPO / "infra/terraform-gcp/templates/docker-compose.gcp.yml.tftpl"


def _fake_curl(tmp_path: Path) -> Path:
    binary = tmp_path / "bin" / "curl"
    binary.parent.mkdir()
    binary.write_text(
        """#!/usr/bin/env python3
import base64
import json
import os
import sys

url = sys.argv[-1]
marker = "/secrets/"
if marker not in url:
    raise SystemExit(22)
full_name = url.split(marker, 1)[1].split("/versions/", 1)[0]
prefix = os.environ["FAKE_SECRET_PREFIX"]
if not full_name.startswith(prefix):
    raise SystemExit(22)
name = full_name[len(prefix):]
values = json.loads(os.environ["FAKE_SECRET_VALUES"])
if name not in values:
    raise SystemExit(22)
encoded = base64.b64encode(values[name].encode()).decode()
print(json.dumps({"payload": {"data": encoded}}))
""",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    return binary.parent


def _run_helper(
    tmp_path: Path,
    values: dict[str, str],
    env_file: Path,
    *,
    mode: str | None = None,
    include_env_file: bool = True,
):
    fake_bin = _fake_curl(tmp_path)
    prefix = "omega-test-"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "OMEGA_GCP_PROJECT_ID": "omega-project",
        "OMEGA_GCP_SECRET_PREFIX": prefix,
        "OMEGA_GCP_ACCESS_TOKEN": "metadata-token-for-test",
        "FAKE_SECRET_PREFIX": prefix,
        "FAKE_SECRET_VALUES": json.dumps(values),
    }
    if include_env_file:
        env["OMEGA_ENV_FILE"] = str(env_file)
    else:
        env.pop("OMEGA_ENV_FILE", None)
    if mode is not None:
        env["OMEGA_SECRET_HYDRATION_MODE"] = mode
    return subprocess.run(
        ["bash", str(HELPER)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _valid_values() -> dict[str, str]:
    return {
        "control_room_evidence_signing_key_id": "key-id-2",
        "control_room_evidence_signing_key": "signing-secret-value",
        "control_room_evidence_signing_previous_keys": "[]",
        "gcs_hmac_access_key_id": "GOOG1EXAMPLEACCESS",
        "gcs_hmac_secret_access_key": "gcs-hmac-secret-value",
    }


def test_hydration_sets_gcs_pair_and_clears_cross_provider_credentials_atomically(
    tmp_path,
):
    env_file = tmp_path / "infra.env"
    env_file.write_text(
        "KEEP_ME=yes\n"
        "GCS_ACCESS_KEY_ID=old-access\n"
        "GCS_SECRET_ACCESS_KEY=old-secret\n"
        "MINIO_ACCESS_KEY=stale-minio\n"
        "MINIO_SECRET_KEY=stale-minio-secret\n"
        "AWS_ACCESS_KEY_ID=ses-access\n"
        "AWS_SECRET_ACCESS_KEY=ses-secret\n"
        "AWS_SESSION_TOKEN=ses-session-token\n",
        encoding="utf-8",
    )
    env_file.chmod(0o644)
    values = _valid_values()

    result = _run_helper(tmp_path, values, env_file)

    assert result.returncode == 0, result.stderr
    content = env_file.read_text(encoding="utf-8")
    assert "KEEP_ME=yes" in content
    assert "GCS_ACCESS_KEY_ID=GOOG1EXAMPLEACCESS" in content
    assert "GCS_SECRET_ACCESS_KEY=gcs-hmac-secret-value" in content
    assert "MINIO_ACCESS_KEY=stale-minio\n" in content
    assert "MINIO_SECRET_KEY=stale-minio-secret\n" in content
    assert "AWS_ACCESS_KEY_ID=\n" in content
    assert "AWS_SECRET_ACCESS_KEY=\n" in content
    assert "AWS_SESSION_TOKEN=\n" in content
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    combined_output = result.stdout + result.stderr
    for secret in values.values():
        assert secret not in combined_output


def test_hydration_failure_keeps_previous_environment_byte_for_byte(tmp_path):
    env_file = tmp_path / "infra.env"
    original = b"KEEP_ME=yes\nGCS_ACCESS_KEY_ID=old\nGCS_SECRET_ACCESS_KEY=old-secret\n"
    env_file.write_bytes(original)
    values = _valid_values()
    values.pop("gcs_hmac_secret_access_key")

    result = _run_helper(tmp_path, values, env_file)

    assert result.returncode != 0
    assert env_file.read_bytes() == original
    assert "gcs_hmac_secret_access_key" in result.stderr
    assert "signing-secret-value" not in result.stdout + result.stderr


def test_check_mode_validates_all_secrets_without_any_environment_write(tmp_path):
    env_file = tmp_path / "infra.env"
    original = b"KEEP_ME=byte-for-byte\nGCS_ACCESS_KEY_ID=old\n"
    env_file.write_bytes(original)
    env_file.chmod(0o644)

    result = _run_helper(
        tmp_path,
        _valid_values(),
        env_file,
        mode="check",
        include_env_file=False,
    )

    assert result.returncode == 0, result.stderr
    assert "environment unchanged" in result.stdout
    assert env_file.read_bytes() == original
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o644
    assert not list(tmp_path.glob(".omega-secret-updates.*"))


def test_startup_and_day2_deploy_share_hydration_and_rollback_contract():
    startup = STARTUP.read_text(encoding="utf-8")
    remote = CANONICAL_REMOTE.read_text(encoding="utf-8")
    gcp_compose = GCP_COMPOSE.read_text(encoding="utf-8")
    helper_name = "infra/terraform-gcp/release/hydrate-runtime-secrets.sh"

    assert helper_name in startup
    assert helper_name in remote
    assert 'set_env MINIO_ACCESS_KEY ""' not in startup
    assert 'set_env MINIO_SECRET_KEY ""' not in startup
    assert 'MINIO_ACCESS_KEY: ""' in gcp_compose
    assert 'MINIO_SECRET_KEY: ""' in gcp_compose
    assert 'AWS_ACCESS_KEY_ID: ""' in gcp_compose
    assert 'AWS_SECRET_ACCESS_KEY: ""' in gcp_compose
    assert 'AWS_SESSION_TOKEN: ""' in gcp_compose
    assert "$${AWS_ACCESS_KEY_ID:-}" not in gcp_compose
    assert "$${AWS_SECRET_ACCESS_KEY:-}" not in gcp_compose
    for airflow_minio_name in (
        "AIRFLOW_VAR_MINIO_BUCKET",
        "AIRFLOW_VAR_MINIO_ENDPOINT",
        "AIRFLOW_VAR_MINIO_ACCESS_KEY",
        "AIRFLOW_VAR_MINIO_SECRET_KEY",
        "AIRFLOW_VAR_MINIO_SECURE",
    ):
        assert gcp_compose.count(f'      {airflow_minio_name}: ""') == 3
        assert f"{airflow_minio_name}: $${{" not in gcp_compose
    assert gcp_compose.count("\n      SES_INBOX_AWS_ACCESS_KEY_ID:") == 3
    assert gcp_compose.count("\n      SES_INBOX_AWS_SECRET_ACCESS_KEY:") == 3
    assert "set_env AWS_REGION auto" in startup
    assert "restore_environment" in remote
    assert "infra.env.candidate.before" in remote
    assert "infra.env.shared.before" in remote
    assert 'if [[ "${MUTATED:-0}" -eq 1 ]]' in remote
    assert "aborted before service/database mutation; runtime left running" in remote
    assert "OMEGA_SECRET_HYDRATION_MODE=check" in remote


def test_secret_helper_and_canonical_deploy_parse_as_bash():
    for path in (HELPER, CANONICAL_REMOTE):
        result = subprocess.run(
            ["bash", "-n", str(path)], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, f"{path.name}: {result.stderr}"


@pytest.mark.parametrize(
    "cartridge",
    ("replicon", "hubspot", "salesforce", "sap_hcm", "sap_s4hana"),
)
def test_gcp_cartridges_resolve_only_native_gcs_credentials(cartridge):
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO / "cartridges" / cartridge),
        "LAKEHOUSE_PROVIDER": "gcs",
        "LAKEHOUSE_ENDPOINT": "storage.googleapis.com",
        "GCS_BUCKET": "omega-gcs",
        "GCS_ACCESS_KEY_ID": "native-gcs-access",
        "GCS_SECRET_ACCESS_KEY": "native-gcs-secret",
        "MINIO_ENDPOINT": "stale-minio:9000",
        "MINIO_BUCKET": "stale-minio-bucket",
        "MINIO_ACCESS_KEY": "stale-minio-access",
        "MINIO_SECRET_KEY": "stale-minio-secret",
        "DATABASE_URL": "postgresql://example.invalid/db",
        "PG_USER": "postgres",
        "PG_PASSWORD": "postgres",
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from app.core.config import settings; "
                "assert settings.minio_endpoint == 'storage.googleapis.com'; "
                "assert settings.minio_bucket == 'omega-gcs'; "
                "assert settings.minio_access_key == 'native-gcs-access'; "
                "assert settings.minio_secret_key == 'native-gcs-secret'; "
                "assert settings.minio_secure is True"
            ),
        ],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
