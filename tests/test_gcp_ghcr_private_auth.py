from __future__ import annotations

import base64
import os
import subprocess
from pathlib import Path

import pytest


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
    project_roles = iam.split('resource "google_secret_manager_secret_iam_member"', 1)[
        0
    ]

    assert 'resource "google_secret_manager_secret" "ghcr_pull_credentials"' in secrets
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


@pytest.mark.parametrize(
    ("github_scopes", "expected_code"),
    [("read:packages", 0), ("read:packages, repo", 10), ("none", 10)],
)
def test_auth_runner_redacts_credentials_enforces_read_only_scope_and_cleans_up(
    tmp_path: Path, github_scopes: str, expected_code: int
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
elif [[ "$args" == *"api.github.com/users/emmanuelnavaromero02-commits/packages/container/"* ]]; then
  package="${{@: -1}}"
  package="${{package##*/}}"
  previous=""
  header_path=""
  output_path=""
  for arg in "$@"; do
    [[ "$previous" == "--dump-header" ]] && header_path="$arg"
    [[ "$previous" == "--output" ]] && output_path="$arg"
    previous="$arg"
  done
  test -n "$header_path"
  test -n "$output_path"
  scope="${{TEST_GITHUB_SCOPES:-read:packages}}"
  [[ "$scope" == "none" ]] && scope=""
  printf 'HTTP/1.1 200 OK\r\nX-OAuth-Scopes: %s\r\n\r\n' "$scope" > "$header_path"
  printf '{{"name":"%s","package_type":"container","visibility":"private"}}' "$package" > "$output_path"
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
            'test "$OMEGA_GHCR_PRIVATE_PACKAGES_VERIFIED" = 1; '
            'test -s "$DOCKER_CONFIG/config.json"; printf "%s\\n" command-ok',
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "OMEGA_GCP_ENVIRONMENT": "staging",
            "OMEGA_GHCR_PULL_SECRET_VERSION": "7",
            "OMEGA_GHCR_AUTH_TEST_NON_ROOT": "1",
            "OMEGA_GHCR_AUTH_TEST_TMPDIR": str(tmp_path / "auth-root"),
            "TEST_DOCKER_CONFIG_RECORD": str(docker_config_record),
            "TEST_CURL_CONFIG_RECORD": str(curl_config_record),
            "TEST_GITHUB_SCOPES": github_scopes,
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == expected_code, result.stderr
    for secret in (
        "server-owned-token",
        "metadata-access-token",
        payload,
    ):
        assert secret not in result.stdout
        assert secret not in result.stderr
    if expected_code != 0:
        assert "scope is missing or exceeds read:packages" in result.stderr
        assert "command-ok" not in result.stdout
        assert not docker_config_record.exists()
        assert curl_config_record.exists()
        assert not Path(curl_config_record.read_text(encoding="utf-8")).exists()
        return
    assert "command-ok" in result.stdout
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
            "OMEGA_GHCR_AUTH_TEST_NON_ROOT": "1",
            "OMEGA_GHCR_AUTH_TEST_TMPDIR": str(tmp_path / "auth-root"),
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 5
    assert "explicit numeric version" in result.stderr
    assert not curl_marker.exists()


@pytest.mark.parametrize(
    "image_tag", ["v1.45.207-beta", f"candidate-{'b' * 40}"]
)
def test_preflight_pulls_exactly_15_and_writes_digest_lock(
    tmp_path: Path, image_tag: str
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_config = tmp_path / "docker-config"
    docker_config.mkdir()
    (docker_config / "config.json").write_text("{}\n", encoding="utf-8")
    docker_root = tmp_path / "docker-root"
    docker_root.mkdir()
    pull_record = tmp_path / "pulls"
    lock_file = tmp_path / "release-images.env"
    digest = "a" * 64
    revision = "b" * 40
    version = "1.45.207-beta"

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
    if [[ "$*" == *org.opencontainers.image.revision* ]]; then
      printf '%s\n%s\n%s\n' '{revision}' '{version}' 'https://github.com/emmanuelnavaromero02-commits/CONSOLA-V1'
    else
      printf '["%s@sha256:{digest}"]\n' "$repository"
    fi
    ;;
  *) exit 14 ;;
esac
""",
    )
    _write_executable(
        fake_bin / "df",
        "#!/bin/sh\nprintf 'Avail\\n42949672960\\n'\n",
    )

    result = subprocess.run(
        [
            "bash",
            str(PREFLIGHT),
            "emmanuelnavaromero02-commits",
            image_tag,
            revision,
            version,
            str(lock_file),
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DOCKER_CONFIG": str(docker_config),
            "OMEGA_GHCR_AUTH_ACTIVE": "1",
            "OMEGA_GHCR_PRIVATE_PACKAGES_VERIFIED": "1",
            "OMEGA_GHCR_PREFLIGHT_TEST_MODE": "1",
            "OMEGA_GHCR_PREFLIGHT_TEST_DOCKER_ROOT": str(docker_root),
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
    assert all(f":{image_tag}@sha256:" in line for line in lock_lines)
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
        [
            "bash",
            str(PREFLIGHT),
            "emmanuelnavaromero02-commits",
            "latest",
            "b" * 40,
            "1.45.207-beta",
            str(tmp_path / "lock.env"),
        ],
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


def test_preflight_rejects_low_docker_headroom_before_first_pull(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_marker = tmp_path / "docker-ran"
    docker_config = tmp_path / "docker-config"
    docker_config.mkdir()
    (docker_config / "config.json").write_text("{}\n", encoding="utf-8")
    docker_root = tmp_path / "docker-root"
    docker_root.mkdir()
    _write_executable(
        fake_bin / "docker",
        f"#!/bin/sh\ntouch {docker_marker!s}\nexit 99\n",
    )
    _write_executable(
        fake_bin / "df",
        "#!/bin/sh\nprintf 'Avail\\n1073741824\\n'\n",
    )
    revision = "b" * 40
    result = subprocess.run(
        [
            "bash",
            str(PREFLIGHT),
            "emmanuelnavaromero02-commits",
            f"candidate-{revision}",
            revision,
            "1.45.207-beta",
            str(tmp_path / "lock.env"),
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DOCKER_CONFIG": str(docker_config),
            "OMEGA_GHCR_AUTH_ACTIVE": "1",
            "OMEGA_GHCR_PRIVATE_PACKAGES_VERIFIED": "1",
            "OMEGA_GHCR_PREFLIGHT_TEST_MODE": "1",
            "OMEGA_GHCR_PREFLIGHT_TEST_DOCKER_ROOT": str(docker_root),
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 6
    assert "30 GiB pre-pull gate" in result.stderr
    assert not docker_marker.exists()
