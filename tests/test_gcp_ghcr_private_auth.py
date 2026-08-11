from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest
from scripts import release_images


ROOT = Path(__file__).resolve().parents[1]
GCP_TERRAFORM = ROOT / "infra/terraform-gcp"
AUTH_RUNNER = GCP_TERRAFORM / "release/ghcr-auth-run.sh"
PREFLIGHT = GCP_TERRAFORM / "release/preflight-release-images.sh"
LOCK_PATH_VALIDATOR = GCP_TERRAFORM / "release/validate-lock-output.py"
AUTHORITY_VALIDATOR = GCP_TERRAFORM / "release/validate-image-authority.py"
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


def _crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return (~crc) & 0xFFFFFFFF


def test_crc32c_reference_vector() -> None:
    assert _crc32c(b"123456789") == 0xE3069283


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o700)


def _temporary_auth_runner(tmp_path: Path) -> Path:
    """Instrument a test-only copy without weakening the shipped root gate."""

    source = _read(AUTH_RUNNER)
    guard_start = source.index('if [[ "${EUID:-$(id -u)}" -ne 0 ]]')
    guard_end = source.index("\nfi", guard_start) + len("\nfi")
    source = source[:guard_start] + source[guard_end:]
    auth_start = source.index('auth_root="/run/omega-gcp-ghcr-auth"')
    auth_end = source.index("# Canonical operations", auth_start)
    auth_root = tmp_path / "auth-root"
    replacement = (
        f"auth_root={shlex.quote(str(auth_root))}\n"
        'install -d -m 0700 "$auth_root"\n'
        '[[ ! -L "$auth_root" && -d "$auth_root" ]] || exit 6\n'
    )
    source = source[:auth_start] + replacement + source[auth_end:]
    docker_guard = (
        'if [[ -L "$docker_config" || "$(stat -c \'%u:%g:%a\' '
        '"$docker_config")" != "0:0:700" ]]; then\n'
        '  echo "ERROR: ephemeral Docker authentication directory is unsafe." >&2\n'
        "  exit 6\nfi"
    )
    assert source.count(docker_guard) == 1
    source = source.replace(
        docker_guard,
        '[[ ! -L "$docker_config" && -d "$docker_config" ]] || exit 6',
    )
    source = source.replace('chown root:root "$docker_config"', ": # test-owned")
    source = source.replace(
        'chown root:root "$docker_config/config.json"', ": # test-owned"
    )
    source = source.replace('chown root:root "$context_file"', ": # test-owned")
    source = source.replace("info.st_uid != 0", "info.st_uid != os.geteuid()")
    source = source.replace("info.st_gid != 0", "info.st_gid != os.getegid()")
    runner = tmp_path / "ghcr-auth-run.test-only.sh"
    _write_executable(runner, source)
    return runner


def _temporary_preflight(
    tmp_path: Path,
    *,
    docker_root: Path,
    authority_file: Path | None = None,
) -> Path:
    """Patch host paths/mocks only in an ephemeral pytest-owned copy."""

    source = _read(PREFLIGHT)
    guard_start = source.index('if [[ "${EUID:-$(id -u)}" -ne 0 ]]')
    guard_end = source.index("\nfi", guard_start) + len("\nfi")
    source = source[:guard_start] + source[guard_end:]
    context_start = source.index("# BEGIN_CANONICAL_GHCR_CONTEXT")
    context_end = source.index("# END_CANONICAL_GHCR_CONTEXT", context_start)
    context_end = source.index("\nfi", context_end) + len("\nfi")
    replacement = r"""# BEGIN_CANONICAL_GHCR_CONTEXT
context_file="${DOCKER_CONFIG}/auth-context.json"
config_file="${DOCKER_CONFIG}/config.json"
if [[ -L "$DOCKER_CONFIG" || ! -d "$DOCKER_CONFIG" ]]; then
  exit 3
fi
if ! python3 - "$config_file" "$context_file" <<'PY'
import hashlib, json, os, stat, sys
config_path, context_path = sys.argv[1:]
def read_private(path, mode):
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != mode or info.st_nlink != 1:
        raise ValueError
    return open(path, "rb").read()
def exact(pairs):
    value = {}
    for key, item in pairs:
        if key in value: raise ValueError
        value[key] = item
    return value
config = read_private(config_path, 0o600)
raw = read_private(context_path, 0o400)
value = json.loads(raw, object_pairs_hook=exact)
if value != {"config_sha256": hashlib.sha256(config).hexdigest(), "owner": "emmanuelnavaromero02-commits", "private_packages": ["banxico", "inegi", "sec_edgar"], "registry": "ghcr.io", "schema_version": 1}:
    raise ValueError
PY
then
  exit 3
fi"""
    source = source[:context_start] + replacement + source[context_end:]
    lock_root = tmp_path / "image-locks"
    lock_validator = tmp_path / "validate-lock-output.test-only.py"
    lock_validator_source = f"""#!/usr/bin/env python3
import argparse
import importlib.util
import os
from pathlib import Path
spec = importlib.util.spec_from_file_location("lock_validator", {str(LOCK_PATH_VALIDATOR)!r})
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
parser = argparse.ArgumentParser()
parser.add_argument("--lock-file", type=Path, required=True)
parser.add_argument("--target-revision", required=True)
args = parser.parse_args()
module.validate_lock_output(
    args.lock_file,
    args.target_revision,
    lock_root=Path({str(lock_root)!r}),
    managed_parents=(Path({str(lock_root)!r}),),
    uid=os.geteuid(),
    gid=os.getegid(),
)
"""
    _write_executable(lock_validator, lock_validator_source)
    source = source.replace(
        'lock_path_validator="$(dirname -- "${BASH_SOURCE[0]}")/'
        'validate-lock-output.py"',
        f"lock_path_validator={shlex.quote(str(lock_validator))}",
    )
    source = source.replace(
        'docker_root="/var/lib/docker"',
        f"docker_root={shlex.quote(str(docker_root))}",
    )
    source = source.replace(
        'release_helper="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." '
        '&& pwd -P)/scripts/release_images.py"',
        f"release_helper={shlex.quote(str(ROOT / 'scripts/release_images.py'))}",
    )
    source = source.replace(
        'authority_validator="$(dirname -- "${BASH_SOURCE[0]}")/'
        'validate-image-authority.py"',
        "authority_validator="
        + shlex.quote(str(GCP_TERRAFORM / "release/validate-image-authority.py")),
    )
    source = source.replace("info.st_uid != 0", "info.st_uid != os.geteuid()")
    source = source.replace("info.st_gid != 0", "info.st_gid != os.getegid()")
    if authority_file is not None:
        verifier = (
            'if ! python3 "$release_helper" "${authority_args[@]}" >/dev/null; then\n'
            '  echo "ERROR: immutable release image authority could not be verified." >&2\n'
            "  exit 7\n"
            "fi"
        )
        assert source.count(verifier) == 1
        source = source.replace(
            verifier,
            f'cp -- {shlex.quote(str(authority_file))} "$temp_authority"',
        )
    preflight = tmp_path / "preflight-release-images.test-only.sh"
    _write_executable(preflight, source)
    return preflight


def _write_test_auth_context(docker_config: Path) -> None:
    docker_config.chmod(0o700)
    config = docker_config / "config.json"
    config.chmod(0o600)
    value = {
        "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
        "owner": "emmanuelnavaromero02-commits",
        "private_packages": ["banxico", "inegi", "sec_edgar"],
        "registry": "ghcr.io",
        "schema_version": 1,
    }
    context = docker_config / "auth-context.json"
    context.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    context.chmod(0o400)


def _new_test_lock_file(tmp_path: Path, revision: str) -> Path:
    root = tmp_path / "image-locks"
    root.mkdir(mode=0o755, exist_ok=True)
    staging = root / f".{revision}.tmp.123"
    staging.mkdir(mode=0o700)
    return staging / "release-images.env"


def _load_lock_validator():
    spec = importlib.util.spec_from_file_location(
        "gcp_lock_output_validator_test", LOCK_PATH_VALIDATOR
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_lock_output_is_revision_bound_root_owned_and_noreplace(
    tmp_path: Path,
) -> None:
    module = _load_lock_validator()
    revision = "b" * 40
    lock_file = _new_test_lock_file(tmp_path, revision)
    lock_root = tmp_path / "image-locks"
    options = {
        "lock_root": lock_root,
        "managed_parents": (lock_root,),
        "uid": os.geteuid(),
        "gid": os.getegid(),
    }
    module.validate_lock_output(lock_file, revision, **options)

    lock_file.parent.chmod(0o777)
    with pytest.raises(ValueError, match="unsafe managed"):
        module.validate_lock_output(lock_file, revision, **options)
    lock_file.parent.chmod(0o700)

    traversal = lock_file.parent / ".." / lock_file.parent.name / lock_file.name
    with pytest.raises(ValueError, match="outside the canonical"):
        module.validate_lock_output(traversal, revision, **options)

    lock_file.symlink_to(tmp_path / "attacker-lock")
    with pytest.raises(ValueError, match="outputs must be new"):
        module.validate_lock_output(lock_file, revision, **options)

    source = _read(PREFLIGHT)
    assert source.index("os.fsync(descriptor)") < source.index("os.link(source")
    assert source.index("os.link(source") < source.index("os.fsync(directory_fd)")
    assert 'mv -f -- "$temp_lock"' not in source


def _published_authority_document(revision: str) -> dict[str, object]:
    digest = f"sha256:{'a' * 64}"
    return {
        "authority_mode": "published",
        "candidate_workflow": {
            "head_sha": revision,
            "run_attempt": "1",
            "run_id": "7",
        },
        "image_tag": "v1.45.207-beta",
        "images": [
            {
                "digest": digest,
                "reference": (
                    "ghcr.io/emmanuelnavaromero02-commits/" f"{image.service}@{digest}"
                ),
                "service": image.service,
                "visibility": (
                    "private"
                    if image.service in release_images.PROTECTED_PRIVATE_PACKAGES
                    else "public"
                ),
            }
            for image in release_images.IMAGES
        ],
        "labels_authoritative": False,
        "legacy_image_ids": None,
        "legacy_tag_commit": None,
        "manifest_digest": f"sha256:{'c' * 64}",
        "release_tag": "v1.45.207-beta",
        "schema_version": 1,
        "secrets_included": False,
        "source_sha": revision,
        "tag_object_sha": "d" * 40,
        "version": "1.45.207-beta",
    }


def _run_authority_validator(
    authority: Path, digests: Path, revision: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "python3",
            str(AUTHORITY_VALIDATOR),
            "--authority",
            str(authority),
            "--digests",
            str(digests),
            "--mode",
            "published",
            "--source-sha",
            revision,
            "--version",
            "1.45.207-beta",
            "--image-tag",
            "v1.45.207-beta",
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_authority_json_is_exact_bounded_and_private_exactly_three(
    tmp_path: Path,
) -> None:
    revision = "b" * 40
    document = _published_authority_document(revision)
    authority = tmp_path / "authority.json"
    digests = tmp_path / "digests.tsv"
    authority.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    authority.chmod(0o600)
    digests.write_text("\n", encoding="utf-8")
    digests.chmod(0o600)

    accepted = _run_authority_validator(authority, digests, revision)
    assert accepted.returncode == 0, accepted.stderr
    assert len(digests.read_text(encoding="utf-8").splitlines()) == 15
    assert sum(row["visibility"] == "private" for row in document["images"]) == 3

    document["images"][0]["visibility"] = "private"
    authority.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    rejected_privacy = _run_authority_validator(authority, digests, revision)
    assert rejected_privacy.returncode != 0
    assert "privacy differs" in rejected_privacy.stderr

    valid = _published_authority_document(revision)
    raw = json.dumps(valid, sort_keys=True, separators=(",", ":"))
    authority.write_text('{"schema_version":1,' + raw[1:] + "\n", encoding="utf-8")
    duplicate = _run_authority_validator(authority, digests, revision)
    assert duplicate.returncode != 0
    assert "duplicate JSON key" in duplicate.stderr

    authority.write_text('{"padding":"' + ("x" * 1_048_577) + '"}\n', encoding="utf-8")
    oversized = _run_authority_validator(authority, digests, revision)
    assert oversized.returncode != 0
    assert "unsafe" in oversized.stderr


def test_registry_private_auth_file_is_nofollow_confined_and_exact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical_root = tmp_path / "omega-gcp-ghcr-auth"
    canonical_root.mkdir(mode=0o700)
    auth_root = canonical_root / "omega-gcp-ghcr-auth.A1b2C3"
    auth_root.mkdir(mode=0o700)
    monkeypatch.setattr(release_images, "GCP_GHCR_AUTH_ROOT", canonical_root)
    monkeypatch.setattr(release_images, "GCP_GHCR_AUTH_OWNER_UID", os.geteuid())
    monkeypatch.setattr(release_images, "GCP_GHCR_AUTH_OWNER_GID", os.getegid())
    auth = base64.b64encode(b"release-reader:server-owned-token-12345").decode("ascii")
    config = auth_root / "config.json"
    config.write_text(
        json.dumps({"auths": {"ghcr.io": {"auth": auth}}}), encoding="utf-8"
    )
    config.chmod(0o600)

    assert isinstance(
        release_images.RegistryClient.from_private_auth_file(config),
        release_images.RegistryClient,
    )

    config.chmod(0o644)
    with pytest.raises(release_images.ReleaseImageError, match="ownership or mode"):
        release_images.RegistryClient.from_private_auth_file(config)
    config.chmod(0o600)
    linked_root = canonical_root / "omega-gcp-ghcr-auth.D4e5F6"
    linked_root.mkdir(mode=0o700)
    linked = linked_root / "config.json"
    linked.symlink_to(config)
    with pytest.raises(release_images.ReleaseImageError):
        release_images.RegistryClient.from_private_auth_file(linked)

    config.write_text(
        json.dumps(
            {
                "auths": {
                    "ghcr.io": {"auth": auth},
                    "docker.io": {"auth": auth},
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(release_images.ReleaseImageError, match="registry-exclusive"):
        release_images.RegistryClient.from_private_auth_file(config)


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
    assert '"$GHCR_TOKEN"' not in runner
    assert "GHCR_TOKEN=" not in runner
    assert "unset GHCR_TOKEN GITHUB_TOKEN" in runner
    assert "github.token" not in runner
    assert "OMEGA_GHCR_AUTH_TEST" not in runner
    assert 'if [[ "${EUID:-$(id -u)}" -ne 0 ]]' in runner
    preflight = _read(PREFLIGHT)
    environment_names = set(re.findall(r"\bOMEGA_[A-Z0-9_]+", preflight))
    assert not {
        name for name in environment_names if re.search(r"(?:^|_)TEST(?:_|$)", name)
    }
    assert "TEST_AUTHORITY_FILE" not in preflight
    assert 'docker_root="/var/lib/docker"' in preflight
    assert 'if ! python3 "$release_helper" "${authority_args[@]}"' in preflight
    assert "secret_version" not in _read(GCP_TERRAFORM / "variables.tf")
    assert "DOCKER_CONFIG" not in _read(
        GCP_TERRAFORM / "templates/docker-compose.gcp.yml.tftpl"
    )


def test_authenticated_curl_paths_disable_environment_proxies() -> None:
    runner = _read(AUTH_RUNNER)
    commands = (
        re.search(
            r"curl (?:(?!\bcurl\b).)*?--config \"\$curl_config\" \"\$secret_url\"",
            runner,
            re.DOTALL,
        ),
        re.search(
            r"curl (?:(?!\bcurl\b).)*?--config \"\$github_config\""
            r"(?:(?!\bcurl\b).)*?api\.github\.com/",
            runner,
            re.DOTALL,
        ),
    )
    assert all(command is not None for command in commands)
    for command in commands:
        assert command is not None
        assert command.group(0).count("--noproxy '*'") == 1


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
    docker_argv_record = tmp_path / "docker-argv"
    context_record = tmp_path / "auth-context.json"
    curl_config_record = tmp_path / "curl-config"
    curl_argv_record = tmp_path / "curl-argv"
    secret_token = "server-owned-token-12345"
    docker_auth = base64.b64encode(f"release-reader:{secret_token}".encode()).decode(
        "ascii"
    )
    credential_payload = json.dumps(
        {"username": "release-reader", "token": secret_token},
        separators=(",", ":"),
    ).encode()
    payload = base64.b64encode(credential_payload).decode("ascii")
    payload_crc32c = _crc32c(credential_payload)
    assert not (tmp_path / "auth-root").exists()
    auth_runner = _temporary_auth_runner(tmp_path)

    _write_executable(
        fake_bin / "curl",
        f"""#!/usr/bin/env bash
set -euo pipefail
args="$*"
noproxy_count=0
previous=""
for arg in "$@"; do
  if [[ "$previous" == "--noproxy" && "$arg" == "*" ]]; then
    noproxy_count=$((noproxy_count + 1))
  fi
  previous="$arg"
done
test "$noproxy_count" = 1
printf '%s\n' "$args" >> "$TEST_CURL_ARGV_RECORD"
if [[ "$args" == *"project/project-id"* ]]; then
  printf '%s' 'omega-test-project'
elif [[ "$args" == *"service-accounts/default/token"* ]]; then
  printf '%s' '{{"access_token":"metadata-access-token","expires_in":3599,"token_type":"Bearer"}}'
elif [[ "$args" == *"versions/7:access"* ]]; then
  previous=""
  for arg in "$@"; do
    if [[ "$previous" == "--config" ]]; then
      grep -q 'metadata-access-token' "$arg"
      printf '%s' "$arg" > "$TEST_CURL_CONFIG_RECORD"
    fi
    previous="$arg"
  done
  printf '%s' '{{"name":"projects/omega-test-project/secrets/omega-staging-ghcr_pull_credentials/versions/7","payload":{{"data":"{payload}","dataCrc32c":"{payload_crc32c}"}}}}'
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
  printf '{{"id":17,"name":"%s","package_type":"container","visibility":"private"}}' "$package" > "$output_path"
else
  exit 12
fi
""",
    )
    _write_executable(
        fake_bin / "docker",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$TEST_DOCKER_ARGV_RECORD"
case "${{1:-}}" in
  login)
    supplied="$(cat)"
    test "$supplied" = "{secret_token}"
    mkdir -p "$DOCKER_CONFIG"
    printf '%s\n' '{{"auths":{{"ghcr.io":{{"auth":"{docker_auth}"}}}}}}' > "$DOCKER_CONFIG/config.json"
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
            str(auth_runner),
            "bash",
            "-c",
            'test "$OMEGA_GHCR_AUTH_ACTIVE" = 1; '
            'test "$OMEGA_GHCR_PRIVATE_PACKAGES_VERIFIED" = 1; '
            'test -z "${GHCR_TOKEN+x}"; test -z "${GITHUB_TOKEN+x}"; '
            'cp "$DOCKER_CONFIG/auth-context.json" "$TEST_CONTEXT_RECORD"; '
            'test -s "$DOCKER_CONFIG/config.json"; printf "%s\\n" command-ok',
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "OMEGA_GCP_ENVIRONMENT": "staging",
            "OMEGA_GHCR_PULL_SECRET_VERSION": "7",
            "TEST_DOCKER_CONFIG_RECORD": str(docker_config_record),
            "TEST_DOCKER_ARGV_RECORD": str(docker_argv_record),
            "TEST_CONTEXT_RECORD": str(context_record),
            "TEST_CURL_CONFIG_RECORD": str(curl_config_record),
            "TEST_CURL_ARGV_RECORD": str(curl_argv_record),
            "TEST_GITHUB_SCOPES": github_scopes,
            "ALL_PROXY": "http://127.0.0.1:9",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "NO_PROXY": "",
            "GHCR_TOKEN": "ambient-token-must-be-discarded",
            "GITHUB_TOKEN": "ambient-workflow-token-must-be-discarded",
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == expected_code, result.stderr
    assert (tmp_path / "auth-root").is_dir(), "first run must create auth_root"
    for secret in (
        secret_token,
        "metadata-access-token",
        payload,
    ):
        assert secret not in result.stdout
        assert secret not in result.stderr
    if docker_argv_record.exists():
        assert secret_token not in docker_argv_record.read_text(encoding="utf-8")
    curl_argv = curl_argv_record.read_text(encoding="utf-8").splitlines()
    assert len(curl_argv) == (6 if expected_code == 0 else 4)
    for argv in curl_argv:
        arguments = argv.split()
        index = arguments.index("--noproxy")
        assert arguments[index + 1] == "*"
        assert arguments.count("--noproxy") == 1
    if expected_code != 0:
        assert "scope is missing or exceeds read:packages" in result.stderr
        assert "command-ok" not in result.stdout
        assert not docker_config_record.exists()
        assert curl_config_record.exists()
        assert not Path(curl_config_record.read_text(encoding="utf-8")).exists()
        return
    assert "command-ok" in result.stdout
    context = json.loads(context_record.read_text(encoding="utf-8"))
    assert set(context) == {
        "config_sha256",
        "owner",
        "private_packages",
        "registry",
        "schema_version",
    }
    assert context["owner"] == "emmanuelnavaromero02-commits"
    assert context["registry"] == "ghcr.io"
    assert context["private_packages"] == ["banxico", "inegi", "sec_edgar"]
    assert secret_token not in context_record.read_text(encoding="utf-8")
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
    auth_runner = _temporary_auth_runner(tmp_path)

    result = subprocess.run(
        ["bash", str(auth_runner), "true"],
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


def test_auth_runner_never_trusts_inherited_flags_or_docker_config(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    curl_marker = tmp_path / "curl-ran"
    command_marker = tmp_path / "command-ran"
    forged = tmp_path / "forged-docker-config"
    forged.mkdir()
    (forged / "config.json").write_text("{}\n", encoding="utf-8")
    _write_executable(
        fake_bin / "curl",
        f"#!/bin/sh\ntouch {shlex.quote(str(curl_marker))}\nexit 99\n",
    )
    auth_runner = _temporary_auth_runner(tmp_path)

    result = subprocess.run(
        ["bash", str(auth_runner), "touch", str(command_marker)],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "OMEGA_GCP_ENVIRONMENT": "staging",
            "OMEGA_GHCR_PULL_SECRET_VERSION": "7",
            "OMEGA_GHCR_AUTH_ACTIVE": "1",
            "OMEGA_GHCR_PRIVATE_PACKAGES_VERIFIED": "1",
            "DOCKER_CONFIG": str(forged),
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 6
    assert curl_marker.exists(), "wrapper must authenticate instead of trusting flags"
    assert not command_marker.exists()


@pytest.mark.parametrize(
    "metadata_payload",
    [
        (
            '{"access_token":"metadata-access-token",'
            '"access_token":"shadow-token-material",'
            '"expires_in":3599,"token_type":"Bearer"}'
        ),
        "{" + '"padding":"' + ("x" * 17_000) + '"}',
    ],
    ids=("duplicate-key", "oversize"),
)
def test_auth_runner_rejects_duplicate_or_oversize_metadata_json(
    tmp_path: Path, metadata_payload: str
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    metadata_file = tmp_path / "metadata.json"
    metadata_file.write_text(metadata_payload, encoding="utf-8")
    docker_marker = tmp_path / "docker-ran"
    _write_executable(
        fake_bin / "curl",
        """#!/usr/bin/env bash
set -euo pipefail
case "$*" in
  *project/project-id*) printf '%s' omega-test-project ;;
  *service-accounts/default/token*) cat "$TEST_METADATA_FILE" ;;
  *) exit 91 ;;
esac
""",
    )
    _write_executable(
        fake_bin / "docker",
        f"#!/bin/sh\ntouch {shlex.quote(str(docker_marker))}\nexit 99\n",
    )
    result = subprocess.run(
        ["bash", str(_temporary_auth_runner(tmp_path)), "true"],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "OMEGA_GCP_ENVIRONMENT": "staging",
            "OMEGA_GHCR_PULL_SECRET_VERSION": "7",
            "TEST_METADATA_FILE": str(metadata_file),
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 7
    assert "invalid service-account token" in result.stderr
    assert not docker_marker.exists()


@pytest.mark.parametrize(
    "failure",
    [
        "duplicate-key",
        "oversize",
        "grammar",
        "crc-missing",
        "crc-mismatch",
        "crc-out-of-range",
    ],
)
def test_auth_runner_rejects_unsafe_secret_json_and_curl_grammar(
    tmp_path: Path, failure: str
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    expected_name = (
        "projects/omega-test-project/secrets/"
        "omega-staging-ghcr_pull_credentials/versions/7"
    )
    valid_token = "server-owned-token-12345"
    valid_bytes = json.dumps(
        {"username": "release-reader", "token": valid_token},
        separators=(",", ":"),
    ).encode()
    encoded_valid = base64.b64encode(valid_bytes).decode()
    valid_crc32c = _crc32c(valid_bytes)
    if failure == "duplicate-key":
        repeated_payload = f'{{"data":"{encoded_valid}","dataCrc32c":"{valid_crc32c}"}}'
        secret_payload = (
            '{"name":"'
            + expected_name
            + '","payload":'
            + repeated_payload
            + ',"payload":'
            + repeated_payload
            + "}"
        )
    elif failure == "oversize":
        secret_payload = '{"padding":"' + ("x" * 17_000) + '"}'
    elif failure == "grammar":
        unsafe_bytes = json.dumps(
            {
                "username": "release-reader",
                "token": 'safe-prefix-material"\\nheader = "Injected: yes',
            },
            separators=(",", ":"),
        ).encode()
        encoded_unsafe = base64.b64encode(unsafe_bytes).decode()
        secret_payload = (
            f'{{"name":"{expected_name}","payload":{{"data":"{encoded_unsafe}",'
            f'"dataCrc32c":"{_crc32c(unsafe_bytes)}"}}}}'
        )
    elif failure == "crc-missing":
        secret_payload = (
            f'{{"name":"{expected_name}","payload":{{"data":"{encoded_valid}"}}}}'
        )
    else:
        checksum = valid_crc32c ^ 1 if failure == "crc-mismatch" else 0x1_0000_0000
        secret_payload = (
            f'{{"name":"{expected_name}","payload":{{"data":"{encoded_valid}",'
            f'"dataCrc32c":"{checksum}"}}}}'
        )
    secret_file = tmp_path / "secret.json"
    secret_file.write_text(secret_payload, encoding="utf-8")
    docker_marker = tmp_path / "docker-ran"
    _write_executable(
        fake_bin / "curl",
        """#!/usr/bin/env bash
set -euo pipefail
case "$*" in
  *project/project-id*) printf '%s' omega-test-project ;;
  *service-accounts/default/token*)
    printf '%s' '{"access_token":"metadata-access-token","expires_in":3599,"token_type":"Bearer"}'
    ;;
  *versions/7:access*) cat "$TEST_SECRET_FILE" ;;
  *) exit 91 ;;
esac
""",
    )
    _write_executable(
        fake_bin / "docker",
        f"#!/bin/sh\ntouch {shlex.quote(str(docker_marker))}\nexit 99\n",
    )
    result = subprocess.run(
        ["bash", str(_temporary_auth_runner(tmp_path)), "true"],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "OMEGA_GCP_ENVIRONMENT": "staging",
            "OMEGA_GHCR_PULL_SECRET_VERSION": "7",
            "TEST_SECRET_FILE": str(secret_file),
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 9
    assert "invalid schema or version" in result.stderr
    assert not docker_marker.exists()


def test_preflight_rejects_env_only_auth_and_config_hash_drift(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_marker = tmp_path / "docker-ran"
    _write_executable(
        fake_bin / "docker",
        f"#!/bin/sh\ntouch {shlex.quote(str(docker_marker))}\nexit 99\n",
    )
    docker_root = tmp_path / "docker-root"
    docker_root.mkdir()
    docker_config = tmp_path / "docker-config"
    docker_config.mkdir()
    config = docker_config / "config.json"
    config.write_text("{}\n", encoding="utf-8")
    config.chmod(0o600)
    preflight = _temporary_preflight(tmp_path, docker_root=docker_root)
    command = [
        "bash",
        str(preflight),
        "emmanuelnavaromero02-commits",
        f"candidate-{'b' * 40}",
        "b" * 40,
        "1.45.207-beta",
        str(tmp_path / "lock.env"),
    ]
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DOCKER_CONFIG": str(docker_config),
        "OMEGA_GHCR_AUTH_ACTIVE": "1",
        "OMEGA_GHCR_PRIVATE_PACKAGES_VERIFIED": "1",
        "OMEGA_GCP_IMAGE_AUTHORITY_MODE": "candidate",
    }

    missing_proof = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert missing_proof.returncode == 3
    assert not docker_marker.exists()

    _write_test_auth_context(docker_config)
    config.chmod(0o600)
    config.write_text('{"auths":{}}\n', encoding="utf-8")
    config.chmod(0o600)
    drifted = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert drifted.returncode == 3
    assert not docker_marker.exists()


@pytest.mark.parametrize(
    ("image_tag", "authority_mode"),
    [
        ("v1.45.207-beta", "published"),
        (f"candidate-{'b' * 40}", "candidate"),
        ("v1.45.207-beta", "legacy-rollback"),
    ],
)
def test_preflight_pulls_exactly_15_and_writes_digest_lock(
    tmp_path: Path, image_tag: str, authority_mode: str
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_config = tmp_path / "docker-config"
    docker_config.mkdir()
    (docker_config / "config.json").write_text("{}\n", encoding="utf-8")
    _write_test_auth_context(docker_config)
    docker_root = tmp_path / "docker-root"
    docker_root.mkdir()
    pull_record = tmp_path / "pulls"
    digest = "a" * 64
    revision = "b" * 40
    lock_file = _new_test_lock_file(tmp_path, revision)
    version = "1.45.207-beta"
    authority_file = tmp_path / "authority.json"
    legacy_image_id = f"sha256:{'e' * 64}"
    ordered_images = [
        "console",
        "workspace",
        "refinement",
        "vault",
        "mcp-infra",
        "airflow",
        "replicon",
        "hubspot",
        "banxico",
        "inegi",
        "sec_edgar",
        "sap_hcm",
        "sap_s4hana",
        "sap_successfactors",
        "salesforce",
    ]
    authority_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "authority_mode": authority_mode,
                "source_sha": revision,
                "release_tag": f"v{version}",
                "image_tag": image_tag,
                "version": version,
                "manifest_digest": (
                    None
                    if authority_mode == "legacy-rollback"
                    else f"sha256:{'c' * 64}"
                ),
                "tag_object_sha": "d" * 40 if authority_mode == "published" else None,
                "candidate_workflow": (
                    {
                        "run_id": "7",
                        "run_attempt": "1",
                        "head_sha": revision,
                        "controller_attestation_sha256": "c" * 64,
                    }
                    if authority_mode == "candidate"
                    else (
                        {
                            "run_id": "7",
                            "run_attempt": "1",
                            "head_sha": revision,
                        }
                        if authority_mode == "published"
                        else None
                    )
                ),
                "legacy_image_ids": (
                    {service: legacy_image_id for service in ordered_images}
                    if authority_mode == "legacy-rollback"
                    else None
                ),
                "legacy_tag_commit": (
                    "f" * 40 if authority_mode == "legacy-rollback" else None
                ),
                "images": [
                    {
                        "service": service,
                        "digest": f"sha256:{digest}",
                        "reference": (
                            "ghcr.io/emmanuelnavaromero02-commits/"
                            f"{service}@sha256:{digest}"
                        ),
                        "visibility": (
                            "private"
                            if service in {"banxico", "inegi", "sec_edgar"}
                            else (
                                "unknown"
                                if authority_mode == "legacy-rollback"
                                else "public"
                            )
                        ),
                    }
                    for service in ordered_images
                ],
                "labels_authoritative": False,
                "secrets_included": False,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )

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
    repository="${{reference%@*}}"
    if [[ "$*" == *'{{{{.Id}}}}'* ]]; then
      printf '%s\n' '{legacy_image_id}'
    elif [[ "$*" == *org.opencontainers.image.revision* ]]; then
      if [[ "${{OMEGA_GCP_IMAGE_AUTHORITY_MODE:-}}" == legacy-rollback ]]; then
        printf '\n\n\n'
      else
        printf '%s\n%s\n%s\n' '{revision}' '{version}' 'https://github.com/emmanuelnavaromero02-commits/CONSOLA-V1'
      fi
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
    preflight = _temporary_preflight(
        tmp_path,
        docker_root=docker_root,
        authority_file=authority_file,
    )

    result = subprocess.run(
        [
            "bash",
            str(preflight),
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
            "OMEGA_GCP_IMAGE_AUTHORITY_MODE": authority_mode,
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
    assert {line.rsplit("/", 1)[1].split("@", 1)[0] for line in pulls} == RELEASE_IMAGES
    assert all(line.endswith(f"@sha256:{digest}") for line in pulls)
    lock_lines = [
        line
        for line in lock_file.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    assert len(lock_lines) == 15
    assert len({line.split("=", 1)[0] for line in lock_lines}) == 15
    assert all(f":{image_tag}@sha256:" in line for line in lock_lines)
    assert lock_file.stat().st_mode & 0o777 == 0o600
    assert (
        Path(f"{lock_file}.authority.json").read_bytes() == authority_file.read_bytes()
    )


def test_preflight_rejects_mutable_latest_before_docker(tmp_path: Path) -> None:
    docker_config = tmp_path / "docker-config"
    docker_config.mkdir()
    (docker_config / "config.json").write_text("{}\n", encoding="utf-8")
    _write_test_auth_context(docker_config)
    docker_root = tmp_path / "docker-root"
    docker_root.mkdir()
    docker_marker = tmp_path / "docker-ran"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "docker",
        f"#!/bin/sh\ntouch {docker_marker!s}\nexit 99\n",
    )

    preflight = _temporary_preflight(tmp_path, docker_root=docker_root)
    result = subprocess.run(
        [
            "bash",
            str(preflight),
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
            "OMEGA_GHCR_PRIVATE_PACKAGES_VERIFIED": "1",
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
    _write_test_auth_context(docker_config)
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
    lock_file = _new_test_lock_file(tmp_path, revision)
    preflight = _temporary_preflight(tmp_path, docker_root=docker_root)
    result = subprocess.run(
        [
            "bash",
            str(preflight),
            "emmanuelnavaromero02-commits",
            f"candidate-{revision}",
            revision,
            "1.45.207-beta",
            str(lock_file),
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DOCKER_CONFIG": str(docker_config),
            "OMEGA_GHCR_AUTH_ACTIVE": "1",
            "OMEGA_GHCR_PRIVATE_PACKAGES_VERIFIED": "1",
            "OMEGA_GCP_IMAGE_AUTHORITY_MODE": "candidate",
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 6
    assert "30 GiB pre-pull gate" in result.stderr
    assert not docker_marker.exists()
