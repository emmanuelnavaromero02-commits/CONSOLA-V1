from __future__ import annotations

import base64
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GCP_TERRAFORM = ROOT / "infra/terraform-gcp"
AUTH_RUNNER = GCP_TERRAFORM / "release/ghcr-auth-run.sh"
PREFLIGHT = GCP_TERRAFORM / "release/preflight-release-images.sh"
RELEASE_OVERLAY = GCP_TERRAFORM / "release/docker-compose.release.yml"

RELEASE_IMAGES = {
    "airflow",
    "banxico",
    "console",
    "hubspot",
    "inegi",
    "mcp-infra",
    "refinement",
    "replicon",
    "salesforce",
    "sap_hcm",
    "sap_s4hana",
    "sap_successfactors",
    "sec_edgar",
    "vault",
    "workspace",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o700)


def test_terraform_owns_only_a_resource_scoped_ghcr_secret_container() -> None:
    secrets = _read(GCP_TERRAFORM / "secrets.tf")
    iam = _read(GCP_TERRAFORM / "iam.tf")
    project_roles = iam.split(
        'resource "google_secret_manager_secret_iam_member"', 1
    )[0]

    assert (
        'resource "google_secret_manager_secret" "ghcr_pull_credentials"'
        in secrets
    )
    assert 'secret_id = "omega-${var.environment}-ghcr_pull_credentials"' in secrets
    assert "google_secret_manager_secret_version" not in secrets
    assert "secret_data" not in secrets
    assert "roles/secretmanager.secretAccessor" not in project_roles
    assert (
        'resource "google_secret_manager_secret_iam_member" '
        '"app_ghcr_pull_credentials_access"' in iam
    )
    assert "google_secret_manager_secret.ghcr_pull_credentials.secret_id" in iam
    assert 'role      = "roles/secretmanager.secretAccessor"' in iam
    assert 'member    = "serviceAccount:${google_service_account.app.email}"' in iam


def test_gcp_auth_is_host_only_versioned_and_ephemeral() -> None:
    runner = _read(AUTH_RUNNER)

    for marker in (
        "metadata.google.internal/computeMetadata/v1/",
        "--noproxy '*'",
        "secretmanager.googleapis.com/v1/projects/",
        "OMEGA_GHCR_PULL_SECRET_VERSION",
        "versions/${secret_version}:access",
        "--password-stdin",
        "mktemp -d",
        "DOCKER_CONFIG",
        "docker logout ghcr.io",
        "set +x",
    ):
        assert marker in runner

    assert "versions/latest" not in runner
    assert "gcloud " not in runner
    assert "GHCR_TOKEN" not in runner
    assert "github.token" not in runner
    assert "secret_version" not in _read(GCP_TERRAFORM / "variables.tf")
    assert "DOCKER_CONFIG" not in _read(
        GCP_TERRAFORM / "templates/docker-compose.gcp.yml.tftpl"
    )


def test_release_overlay_is_digest_locked_and_disables_build_and_pull() -> None:
    preflight = _read(PREFLIGHT)
    overlay = _read(RELEASE_OVERLAY)

    for image in RELEASE_IMAGES:
        assert f"  {image}" in preflight
    assert "15/15" in preflight
    assert "sha256:[0-9a-f]{64}" in preflight
    assert "@%s" in preflight
    assert overlay.count("build: !reset null") == 17
    assert overlay.count("pull_policy: never") == 17
    for lock_name in (
        "AIRFLOW",
        "BANXICO",
        "CONSOLE",
        "HUBSPOT",
        "INEGI",
        "MCP_INFRA",
        "REFINEMENT",
        "REPLICON",
        "SALESFORCE",
        "SAP_HCM",
        "SAP_S4HANA",
        "SAP_SUCCESSFACTORS",
        "SEC_EDGAR",
        "VAULT",
        "WORKSPACE",
    ):
        assert f"OMEGA_GCP_IMAGE_{lock_name}" in overlay


def test_auth_runner_redacts_credentials_and_removes_docker_config(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_config_record = tmp_path / "docker-config-path"
    curl_config_record = tmp_path / "curl-config"
    payload = base64.b64encode(
        b'{"username":"release-reader","token":"server-owned-token"}'
    ).decode("ascii")

    _write_executable(
        fake_bin / "curl",
        f"""#!/usr/bin/env bash
set -euo pipefail
args="$*"
if [[ "$args" == *"project/project-id"* ]]; then
  printf '%s' 'omega-test-project'
elif [[ "$args" == *"service-accounts/default/token"* ]]; then
  printf '%s' '{{"access_token":"metadata-access-token","token_type":"Bearer"}}'
elif [[ "$args" == *"versions/7:access"* ]]; then
  previous=""
  for arg in "$@"; do
    if [[ "$previous" == "--config" ]]; then
      grep -q 'metadata-access-token' "$arg"
      printf '%s' "$arg" > "$TEST_CURL_CONFIG_RECORD"
    fi
    previous="$arg"
  done
  printf '%s' '{{"name":"projects/omega-test-project/secrets/omega-staging-ghcr_pull_credentials/versions/7","payload":{{"data":"{payload}"}}}}'
else
  exit 12
fi
""",
    )
    _write_executable(
        fake_bin / "docker",
        """#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in
  login)
    supplied="$(cat)"
    test "$supplied" = "server-owned-token"
    mkdir -p "$DOCKER_CONFIG"
    printf '%s\n' '{"auths":{"ghcr.io":{}}}' > "$DOCKER_CONFIG/config.json"
    printf '%s' "$DOCKER_CONFIG" > "$TEST_DOCKER_CONFIG_RECORD"
    ;;
  logout) ;;
  *) exit 13 ;;
esac
""",
    )

    result = subprocess.run(
        [
            "bash",
            str(AUTH_RUNNER),
            "bash",
            "-c",
            'test "$OMEGA_GHCR_AUTH_ACTIVE" = 1; '
            'test -s "$DOCKER_CONFIG/config.json"; printf "%s\\n" command-ok',
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "OMEGA_GCP_ENVIRONMENT": "staging",
            "OMEGA_GHCR_PULL_SECRET_VERSION": "7",
            "OMEGA_GHCR_AUTH_TMPDIR": str(tmp_path),
            "TEST_DOCKER_CONFIG_RECORD": str(docker_config_record),
            "TEST_CURL_CONFIG_RECORD": str(curl_config_record),
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "command-ok" in result.stdout
    for secret in (
        "server-owned-token",
        "metadata-access-token",
        payload,
    ):
        assert secret not in result.stdout
        assert secret not in result.stderr
    assert not Path(docker_config_record.read_text(encoding="utf-8")).exists()
    assert not Path(curl_config_record.read_text(encoding="utf-8")).exists()


def test_auth_runner_rejects_latest_without_contacting_metadata(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    curl_marker = tmp_path / "curl-ran"
    _write_executable(
        fake_bin / "curl",
        f"#!/bin/sh\ntouch {curl_marker!s}\nexit 99\n",
    )

    result = subprocess.run(
        ["bash", str(AUTH_RUNNER), "true"],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "OMEGA_GCP_ENVIRONMENT": "staging",
            "OMEGA_GHCR_PULL_SECRET_VERSION": "latest",
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 5
    assert "explicit numeric version" in result.stderr
    assert not curl_marker.exists()


def test_preflight_pulls_exactly_15_and_writes_digest_lock(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_config = tmp_path / "docker-config"
    docker_config.mkdir()
    (docker_config / "config.json").write_text("{}\n", encoding="utf-8")
    pull_record = tmp_path / "pulls"
    lock_file = tmp_path / "release-images.env"
    digest = "a" * 64

    _write_executable(
        fake_bin / "docker",
        f"""#!/usr/bin/env bash
set -euo pipefail
case "${{1:-}}" in
  pull)
    test "${{2:-}}" = "--quiet"
    printf '%s\n' "${{3:-}}" >> "$TEST_PULL_RECORD"
    ;;
  image)
    test "${{2:-}}" = "inspect"
    reference="${{@: -1}}"
    repository="${{reference%:*}}"
    printf '["%s@sha256:{digest}"]\n' "$repository"
    ;;
  *) exit 14 ;;
esac
""",
    )

    result = subprocess.run(
        [
            "bash",
            str(PREFLIGHT),
            "emmanuelnavaromero02-commits",
            "v1.45.207-beta",
            str(lock_file),
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DOCKER_CONFIG": str(docker_config),
            "OMEGA_GHCR_AUTH_ACTIVE": "1",
            "TEST_PULL_RECORD": str(pull_record),
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "PASS\t15/15" in result.stdout
    pulls = pull_record.read_text(encoding="utf-8").splitlines()
    assert len(pulls) == 15
    assert {line.rsplit("/", 1)[1].split(":", 1)[0] for line in pulls} == RELEASE_IMAGES
    lock_lines = [
        line
        for line in lock_file.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    assert len(lock_lines) == 15
    assert len({line.split("=", 1)[0] for line in lock_lines}) == 15
    assert all(":v1.45.207-beta@sha256:" in line for line in lock_lines)
    assert lock_file.stat().st_mode & 0o777 == 0o600


def test_preflight_rejects_mutable_latest_before_docker(tmp_path: Path) -> None:
    docker_config = tmp_path / "docker-config"
    docker_config.mkdir()
    (docker_config / "config.json").write_text("{}\n", encoding="utf-8")
    docker_marker = tmp_path / "docker-ran"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "docker",
        f"#!/bin/sh\ntouch {docker_marker!s}\nexit 99\n",
    )

    result = subprocess.run(
        ["bash", str(PREFLIGHT), "owner", "latest", str(tmp_path / "lock.env")],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DOCKER_CONFIG": str(docker_config),
            "OMEGA_GHCR_AUTH_ACTIVE": "1",
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 5
    assert not docker_marker.exists()
