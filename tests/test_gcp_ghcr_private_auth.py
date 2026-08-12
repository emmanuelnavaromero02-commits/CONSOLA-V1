from __future__ import annotations

import base64
import hashlib
import http.server
import importlib.util
import json
import os
import re
import signal
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from scripts import release_images


ROOT = Path(__file__).resolve().parents[1]
GCP_TERRAFORM = ROOT / "infra/terraform-gcp"
AUTH_RUNNER = GCP_TERRAFORM / "release/ghcr-auth-run.sh"
PREFLIGHT = GCP_TERRAFORM / "release/preflight-release-images.sh"
LOCK_PATH_VALIDATOR = GCP_TERRAFORM / "release/validate-lock-output.py"
AUTHORITY_VALIDATOR = GCP_TERRAFORM / "release/validate-image-authority.py"
LOCK_PUBLISHER = GCP_TERRAFORM / "release/publish-image-lock.py"
SESSION_HELPER = GCP_TERRAFORM / "release/secure-ghcr-session.py"
BUNDLE_INSTALLER = GCP_TERRAFORM / "release/install-ghcr-release-bundle.py"
BUNDLE_SOURCE_MANIFEST = GCP_TERRAFORM / "release/ghcr-release-bundle.manifest.json"
BUNDLE_CONTRACT = GCP_TERRAFORM / "release/GHCR-RELEASE-BUNDLE-CONTRACT.md"
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


def _write_installed_bundle_manifest(directory: Path, source_sha: str) -> None:
    modes = {
        "ghcr-auth-run.sh": 0o500,
        "secure-ghcr-session.py": 0o400,
        "preflight-release-images.sh": 0o500,
        "validate-image-authority.py": 0o400,
        "validate-lock-output.py": 0o400,
        "publish-image-lock.py": 0o400,
        "release_images.py": 0o400,
    }
    files = {}
    for name, mode in modes.items():
        path = directory / name
        path.chmod(mode)
        raw = path.read_bytes()
        files[name] = {
            "mode": f"{mode:04o}",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size": len(raw),
        }
    manifest = directory / "bundle-manifest.json"
    manifest.write_text(
        json.dumps(
            {"files": files, "schema_version": 1, "source_sha": source_sha},
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    manifest.chmod(0o400)
    directory.chmod(0o500)


def _temporary_auth_runner(tmp_path: Path) -> Path:
    """Instrument trusted paths while retaining the shipped descriptor logic."""

    source = _read(AUTH_RUNNER)
    fake_bin = tmp_path / "bin"
    host_identity = tmp_path / "gcp-host-identity.json"
    host_identity.write_text(
        json.dumps(
            {
                "instance_id": "894064513501",
                "instance_name": "omega-staging-app",
                "project_id": "omega-test-project",
                "schema_version": 1,
                "service_account_email": (
                    "omega-staging-app@omega-test-project.iam.gserviceaccount.com"
                ),
                "zone": "us-central1-a",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    host_identity.chmod(0o400)
    secret_authority = tmp_path / "ghcr-pull-secret-authority.json"
    secret_authority.write_text(
        json.dumps(
            {
                "environment": "staging",
                "project_id": "omega-test-project",
                "schema_version": 1,
                "secret_id": "omega-staging-ghcr_pull_credentials",
                "secret_version_alias": "active",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    secret_authority.chmod(0o400)
    source = source.replace(
        "readonly HOST_IDENTITY=/etc/omega/gcp-host-identity.json",
        f"readonly HOST_IDENTITY={shlex.quote(str(host_identity))}",
    )
    source = source.replace(
        "readonly SECRET_AUTHORITY=/etc/omega/ghcr-pull-secret-authority.json",
        f"readonly SECRET_AUTHORITY={shlex.quote(str(secret_authority))}",
    )
    source = source.replace(
        "readonly PYTHON_BIN=/usr/bin/python3",
        f"readonly PYTHON_BIN={shlex.quote(sys.executable)}",
    )
    source = source.replace(
        "readonly CURL_BIN=/usr/bin/curl",
        f"readonly CURL_BIN={shlex.quote(str(fake_bin / 'curl-test-wrapper'))}",
    )
    _write_executable(
        fake_bin / "curl-test-wrapper",
        """#!/usr/bin/env bash
set -uo pipefail
format=""
previous=""
for argument in "$@"; do
  if [[ "$previous" == "--write-out" ]]; then
    format="$argument"
  fi
  previous="$argument"
done
set +e
output="$("$(dirname -- "$0")/curl" "$@")"
status=$?
set -e
case "$*" in
  *instance/id*) output=894064513501; status=0 ;;
  *instance/name*) output=omega-staging-app; status=0 ;;
  *instance/zone*) output=projects/123456789/zones/us-central1-a; status=0 ;;
  *service-accounts/default/email*)
    output=omega-staging-app@omega-test-project.iam.gserviceaccount.com
    status=0
    ;;
esac
if [[ -n "${TEST_METADATA_IDENTITY_RECORD:-}" && "$*" == *metadata.google.internal* ]]; then
  printf '%s\n' "$*" >> "$TEST_METADATA_IDENTITY_RECORD"
fi
printf '%s' "$output"
http_code=200
case "${TEST_HTTP_FAILURE_TARGET:-}:$*" in
  metadata:*metadata.google.internal*) http_code=503 ;;
  secret:*secretmanager.googleapis.com*) http_code=403 ;;
  github:*api.github.com*) http_code=204 ;;
esac
if [[ "$status" == 0 && "$format" == $'\\n%{http_code}' ]]; then
  printf '\\n%s' "$http_code"
elif [[ "$status" == 0 && "$format" == '%{http_code}' ]]; then
  printf '%s' "$http_code"
fi
exit "$status"
""",
    )
    source = source.replace(
        "readonly DOCKER_BIN=/usr/bin/docker",
        f"readonly DOCKER_BIN={shlex.quote(str(fake_bin / 'docker'))}",
    )
    source = source.replace(
        "readonly DIRNAME_BIN=/usr/bin/dirname",
        f"readonly DIRNAME_BIN={shlex.quote(shutil.which('dirname') or '/usr/bin/dirname')}",
    )
    ca_bundle = tmp_path / "ca-certificates.crt"
    ca_bundle.write_bytes(
        b"-----BEGIN CERTIFICATE-----\n"
        + (b"A" * 1100)
        + b"\n-----END CERTIFICATE-----\n"
    )
    ca_bundle.chmod(0o400)
    source = source.replace(
        "readonly CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt",
        f"readonly CA_BUNDLE={shlex.quote(str(ca_bundle))}",
    )
    for variable, command in (
        ("CHOWN_BIN", "chown"),
        ("CHMOD_BIN", "chmod"),
        ("RM_BIN", "rm"),
        ("MKTEMP_BIN", "mktemp"),
        ("FIND_BIN", "find"),
        ("STAT_BIN", "stat"),
    ):
        resolved = shutil.which(command)
        assert resolved is not None
        source = re.sub(
            rf"readonly {variable}=\S+",
            f"readonly {variable}={shlex.quote(resolved)}",
            source,
            count=1,
        )
    source = source.replace(
        "export PATH=/usr/sbin:/usr/bin:/sbin:/bin",
        ": # test keeps instrumented PATH",
    )
    docker_socket = tmp_path / "docker.sock"
    short_socket = Path("/tmp") / (
        "omega-ghcr-test-"
        + hashlib.sha256(str(tmp_path).encode()).hexdigest()[:16]
        + ".sock"
    )
    short_socket.unlink(missing_ok=True)
    bound_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    bound_socket.bind(str(short_socket))
    bound_socket.close()
    os.link(short_socket, docker_socket)
    short_socket.unlink()
    source = source.replace(
        'docker_socket="/run/docker.sock"',
        f"docker_socket={shlex.quote(str(docker_socket))}",
    )
    guard_start = source.index('if [[ "${EUID:-$("$ID_BIN" -u)}" -ne 0 ]]')
    guard_end = source.index("\nfi", guard_start) + len("\nfi")
    source = source[:guard_start] + source[guard_end:]

    auth_root = tmp_path / "auth-root"
    source = source.replace(
        "--auth-root /run/omega-gcp-ghcr-auth",
        f"--auth-root {shlex.quote(str(auth_root))}",
    )
    source = source.replace(
        'auth_root="/run/omega-gcp-ghcr-auth"',
        f"auth_root={shlex.quote(str(auth_root))}",
    )
    mount_start = source.index('if [[ "$("$FINDMNT_BIN"', source.index("auth_root="))
    mount_end = source.index("\nfi", mount_start) + len("\nfi")
    source = source[:mount_start] + source[mount_end:]
    source = source.replace(
        'auth_root_fd_path="/proc/self/fd/${AUTH_ROOT_FD}"',
        'auth_root_fd_path="$auth_root"',
    )
    source = source.replace(
        '  --expected-directory-identity "$docker_config_identity"',
        '  --expected-directory-identity "$docker_config_identity"\n'
        "  --descriptor-root /dev/fd",
    )
    source = source.replace(
        'if [[ -L "$docker_config" || "$(without_lock_fd "$STAT_BIN" -c \'%u:%g:%a\' "$docker_config")" != "0:0:700" ]]; then',
        'if [[ -L "$docker_config" || ! -d "$docker_config" ]]; then',
    )
    source = source.replace(
        'docker_config_identity="$(without_lock_fd "$STAT_BIN" -c \'%d:%i\' "$docker_config")"',
        'docker_config_identity="$(without_lock_fd "$PYTHON_BIN" -I -c '
        "'import os,sys; value=os.stat(sys.argv[1]); "
        'print(f"{value.st_dev}:{value.st_ino}")\' "$docker_config")"',
    )
    for target in (
        'without_lock_fd "$CHOWN_BIN" root:root "$docker_config"',
        'without_lock_fd "$CHOWN_BIN" root:root "$docker_config/config.json"',
        'without_lock_fd "$CHOWN_BIN" root:root "$context_file"',
    ):
        source = source.replace(target, ": # test-owned")
    source = source.replace("info.st_uid != 0", "info.st_uid != os.geteuid()")
    source = source.replace("info.st_gid != 0", "info.st_gid != os.getegid()")
    source = source.replace("before.st_uid != 0", "before.st_uid != os.geteuid()")
    source = source.replace("before.st_gid != 0", "before.st_gid != os.getegid()")
    source = source.replace("directory.st_uid != 0", "directory.st_uid != os.geteuid()")
    source = source.replace("directory.st_gid != 0", "directory.st_gid != os.getegid()")
    source = source.replace("/proc/self/fd/", "/dev/fd/")
    helper_source = _read(SESSION_HELPER)
    helper_source = helper_source.replace(
        'CANONICAL_DOCKER_HOST = "unix:///run/docker.sock"',
        f"CANONICAL_DOCKER_HOST = {f'unix://{docker_socket}'!r}",
    )
    helper_source = helper_source.replace("/proc/self/fd/", "/dev/fd/")
    helper_source = helper_source.replace(
        'return descriptor, Path(f"/dev/fd/{descriptor}")',
        "return descriptor, canonical  # macOS test harness lacks executable /dev/fd",
    )
    helper_source = helper_source.replace(
        "def docker_exec(args: argparse.Namespace) -> int:\n",
        "_production_docker_environment = _docker_environment\n\n\n"
        "def _test_docker_environment():\n"
        "    environment, pass_fds = _production_docker_environment()\n"
        "    environment.update(\n"
        "        (name, value) for name, value in os.environ.items()\n"
        "        if name.startswith('TEST_') or name == 'OMEGA_GCP_IMAGE_AUTHORITY_MODE'\n"
        "    )\n"
        "    return environment, pass_fds\n\n\n"
        "_docker_environment = _test_docker_environment\n\n\n"
        "def docker_exec(args: argparse.Namespace) -> int:\n",
    )
    bundle_sha = "a" * 40
    bundle_parent = tmp_path / "ghcr-release-bundles"
    bundle_parent.mkdir()
    bundle = bundle_parent / bundle_sha
    bundle.mkdir()
    helper = bundle / "secure-ghcr-session.py"
    helper.write_text(helper_source, encoding="utf-8")
    helper.chmod(0o600)
    source = source.replace(
        "session_helper=/dev/fd/12",
        f"session_helper={shlex.quote(str(helper))}",
    )
    context_record = tmp_path / "auth-context.json"
    preflight = bundle / "preflight-release-images.sh"
    _write_executable(
        preflight,
        f"""#!/bin/bash -p
set -Eeuo pipefail
[[ "$#" == 5 ]]
[[ "$1" == emmanuelnavaromero02-commits ]]
[[ "$2" == candidate-{'b' * 40} || "$2" == v1.45.207-beta ]]
[[ "$3" =~ ^[0-9a-f]{{40}}$ ]]
[[ "$4" == 1.45.207-beta ]]
[[ "$5" == */release-images.env ]]
[[ "${{OMEGA_GHCR_AUTH_ACTIVE:-}}" == 1 ]]
[[ "${{OMEGA_GHCR_PRIVATE_PACKAGES_VERIFIED:-}}" == 1 ]]
[[ "${{DOCKER_CONFIG:-}}" == /dev/fd/8 ]]
[[ -e /dev/fd/6 && -s /dev/fd/7 && -e /dev/fd/8 && -e /dev/fd/9 ]]
cp /dev/fd/6 {shlex.quote(str(context_record))}
printf '%s\n' command-ok
""",
    )
    for source_path, name in (
        (AUTHORITY_VALIDATOR, "validate-image-authority.py"),
        (LOCK_PATH_VALIDATOR, "validate-lock-output.py"),
        (LOCK_PUBLISHER, "publish-image-lock.py"),
        (ROOT / "scripts/release_images.py", "release_images.py"),
    ):
        shutil.copyfile(source_path, bundle / name)
    runner = bundle / "ghcr-auth-run.sh"
    _write_executable(runner, source)
    _write_installed_bundle_manifest(bundle, bundle_sha)
    return runner


def _auth_runner_command(runner: Path, *child_args: str) -> list[str]:
    del child_args
    revision = "b" * 40
    return [
        "/bin/bash",
        "-p",
        str(runner),
        str(runner.parent / "preflight-release-images.sh"),
        "emmanuelnavaromero02-commits",
        f"candidate-{revision}",
        revision,
        "1.45.207-beta",
        str(runner.parent / "release-images.env"),
    ]


def _preflight_command(preflight: Path, *args: str) -> list[str]:
    return ["/bin/bash", "-p", str(preflight), *args]


def _temporary_preflight(
    tmp_path: Path,
    *,
    docker_root: Path,
    authority_file: Path | None = None,
) -> Path:
    """Patch host paths/mocks only in an ephemeral pytest-owned copy."""

    source = _read(PREFLIGHT)
    fake_bin = tmp_path / "bin"
    helper_source = _read(SESSION_HELPER)
    helper_source = helper_source.replace("/proc/self/fd/", "/dev/fd/")
    helper_source = helper_source.replace(
        'return descriptor, Path(f"/dev/fd/{descriptor}")',
        "return descriptor, canonical  # macOS test harness lacks executable /dev/fd",
    )
    helper_source = helper_source.replace(
        '        _validate_bundle_descriptors()\n        if args.operation == "auth-exec":',
        '        if args.operation == "auth-exec":',
    )
    helper_source = helper_source.replace(
        "def docker_exec(args: argparse.Namespace) -> int:\n",
        "_production_docker_environment = _docker_environment\n\n\n"
        "def _test_docker_environment():\n"
        "    environment, pass_fds = _production_docker_environment()\n"
        "    environment.update(\n"
        "        (name, value) for name, value in os.environ.items()\n"
        "        if name.startswith('TEST_') or name == 'OMEGA_GCP_IMAGE_AUTHORITY_MODE'\n"
        "    )\n"
        "    return environment, pass_fds\n\n\n"
        "_docker_environment = _test_docker_environment\n\n\n"
        "def docker_exec(args: argparse.Namespace) -> int:\n",
    )
    helper = tmp_path / "secure-ghcr-session.py"
    helper.write_text(helper_source, encoding="utf-8")
    helper.chmod(0o600)
    bundle_boundary_start = source.index('if [[ "${OMEGA_GHCR_BUNDLE_BOUND:-0}" != "1"')
    bundle_boundary_end = source.index(
        "# BEGIN_CANONICAL_GHCR_LOCK_AND_CONTEXT", bundle_boundary_start
    )
    source = (
        source[:bundle_boundary_start]
        + f"session_helper={shlex.quote(str(helper))}\n"
        + f"authority_validator={shlex.quote(str(AUTHORITY_VALIDATOR))}\n"
        + f"lock_path_validator={shlex.quote(str(LOCK_PATH_VALIDATOR))}\n"
        + f"publisher={shlex.quote(str(LOCK_PUBLISHER))}\n"
        + f"release_helper={shlex.quote(str(ROOT / 'scripts/release_images.py'))}\n"
        + source[bundle_boundary_end:]
    )
    source = source.replace(
        "readonly PYTHON_BIN=/usr/bin/python3",
        f"readonly PYTHON_BIN={shlex.quote(sys.executable)}",
    )
    source = source.replace(
        "readonly DOCKER_BIN=/usr/bin/docker",
        f"readonly DOCKER_BIN={shlex.quote(str(fake_bin / 'docker'))}",
    )
    source = source.replace(
        "readonly DF_BIN=/usr/bin/df",
        f"readonly DF_BIN={shlex.quote(str(fake_bin / 'df'))}",
    )
    for variable, command in (("CHMOD_BIN", "chmod"), ("RM_BIN", "rm")):
        resolved = shutil.which(command)
        assert resolved is not None
        source = source.replace(
            f"readonly {variable}=/usr/bin/{command}",
            f"readonly {variable}={shlex.quote(resolved)}",
        )
    boundary_start = source.index("# BEGIN_CANONICAL_GHCR_LOCK_AND_CONTEXT")
    boundary_end = source.index('owner="$1"', boundary_start)
    replacement = r"""# BEGIN_CANONICAL_GHCR_LOCK_AND_CONTEXT
context_file="${DOCKER_CONFIG}/auth-context.json"
config_file="${DOCKER_CONFIG}/config.json"
if [[ -L "$DOCKER_CONFIG" || ! -d "$DOCKER_CONFIG" ]]; then
  exit 3
fi
if ! "$PYTHON_BIN" -I - "$config_file" "$context_file" <<'PY'
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
if value != {"config_sha256": hashlib.sha256(config).hexdigest(), "owner": "emmanuelnavaromero02-commits", "private_packages": ["banxico", "inegi", "sec_edgar"], "registry": "ghcr.io", "schema_version": 1, "secret_version_resource": "projects/omega-test-project/secrets/omega-staging-ghcr_pull_credentials/versions/7"}:
    raise ValueError
PY
then
  exit 3
fi
without_auth_fds() (
  exec "$@"
)
with_docker_config_fd() (
  exec "$@"
)
with_auth_verifier_fds() (
  exec "$@"
)
bounded_docker() (
  local operation="$1"
  shift
  exec "$PYTHON_BIN" -I "$session_helper" docker-exec \
    --docker "$DOCKER_BIN" --operation "$operation" "$@"
)
"""
    source = source[:boundary_start] + replacement + source[boundary_end:]
    proof_start = source.index(
        'if [[ "$authority_mode" == "published" ]]; then\n  expected_tag_proof='
    )
    proof_end = source.index('docker_root="/var/lib/docker"', proof_start)
    source = source[:proof_start] + source[proof_end:]
    guard_start = source.index('if [[ "${EUID:-$("$ID_BIN" -u)}" -ne 0 ]]')
    guard_end = source.index("\nfi", guard_start) + len("\nfi")
    source = source[:guard_start] + source[guard_end:]
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
parser.add_argument("--authority-mode", required=True)
parser.add_argument("--image-tag", required=True)
parser.add_argument("--version", required=True)
args = parser.parse_args()
module.validate_lock_output(
    args.lock_file,
    args.target_revision,
    authority_mode=args.authority_mode,
    image_tag=args.image_tag,
    version=args.version,
    lock_root=Path({str(lock_root)!r}),
    managed_parents=(Path({str(lock_root)!r}),),
    uid=os.geteuid(),
    gid=os.getegid(),
)
"""
    _write_executable(lock_validator, lock_validator_source)
    source = source.replace(
        f"lock_path_validator={shlex.quote(str(LOCK_PATH_VALIDATOR))}",
        f"lock_path_validator={shlex.quote(str(lock_validator))}",
    )
    source = source.replace(
        'docker_root="/var/lib/docker"',
        f"docker_root={shlex.quote(str(docker_root))}",
    )
    source = source.replace("info.st_uid != 0", "info.st_uid != os.geteuid()")
    source = source.replace("info.st_gid != 0", "info.st_gid != os.getegid()")
    if authority_file is not None:
        verifier = (
            'if ! with_auth_verifier_fds "$PYTHON_BIN" -I "$release_helper" "${authority_args[@]}" >/dev/null; then\n'
            '  echo "ERROR: immutable release image authority could not be verified." >&2\n'
            "  exit 7\n"
            "fi"
        )
        assert source.count(verifier) == 1
        source = source.replace(
            verifier,
            f'cp -- {shlex.quote(str(authority_file))} "$temp_authority"',
        )
    preflight = tmp_path / "preflight-release-images.sh"
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
        "secret_version_resource": (
            "projects/omega-test-project/secrets/"
            "omega-staging-ghcr_pull_credentials/versions/7"
        ),
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


def _load_secure_session_helper():
    helper = GCP_TERRAFORM / "release/secure-ghcr-session.py"
    spec = importlib.util.spec_from_file_location("gcp_secure_session_test", helper)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_bundle_installer():
    spec = importlib.util.spec_from_file_location(
        "gcp_bundle_installer_test", BUNDLE_INSTALLER
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("ignore_term", [False, True], ids=("term", "kill"))
def test_bounded_child_terminates_the_entire_process_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ignore_term: bool,
) -> None:
    module = _load_secure_session_helper()
    pid_file = tmp_path / "descendant.pid"
    term_file = tmp_path / "term-observed"
    child = tmp_path / "bounded-child.sh"
    _write_executable(
        child,
        f"""#!/bin/bash
set -eu
(trap {'\'\' TERM' if ignore_term else shlex.quote(f'touch {term_file}; exit 0') + ' TERM'}; while :; do sleep 1; done) &
echo "$!" > {shlex.quote(str(pid_file))}
while :; do sleep 1; done
""",
    )
    monkeypatch.setattr(module, "TERM_GRACE_SECONDS", 0.3)

    started = time.monotonic()
    with pytest.raises(ValueError, match="hard deadline"):
        module._run_bounded(
            [str(child)],
            environment={"PATH": "/usr/bin:/bin"},
            deadline=1.0,
        )
    assert time.monotonic() - started < 3
    assert pid_file.is_file()
    descendant = int(pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.kill(descendant, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        pytest.fail("bounded child left a descendant alive after group termination")
    if ignore_term:
        assert not term_file.exists()
    else:
        assert term_file.exists()


def test_bounded_child_caps_streams_and_fences_successful_orphans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_secure_session_helper()
    monkeypatch.setattr(module, "TERM_GRACE_SECONDS", 0.2)
    noisy = tmp_path / "noisy.py"
    noisy.write_text(
        "import sys\nsys.stdout.buffer.write(b'x' * 131072)\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="output exceeded"):
        module._run_bounded(
            [sys.executable, str(noisy)],
            environment={"PATH": "/usr/bin:/bin"},
            deadline=5,
            maximum_output=4096,
        )

    split_noisy = tmp_path / "split-noisy.py"
    split_noisy.write_text(
        "import sys\n"
        "sys.stdout.buffer.write(b'x' * 3000)\n"
        "sys.stdout.buffer.flush()\n"
        "sys.stderr.buffer.write(b'y' * 3000)\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="output exceeded"):
        module._run_bounded(
            [sys.executable, str(split_noisy)],
            environment={"PATH": "/usr/bin:/bin"},
            deadline=5,
            maximum_output=4096,
        )

    pid_file = tmp_path / "orphan.pid"
    orphan = tmp_path / "orphan.sh"
    _write_executable(
        orphan,
        f"""#!/bin/bash
set -eu
(trap '' TERM; while :; do sleep 1; done) >/dev/null 2>&1 &
echo "$!" > {shlex.quote(str(pid_file))}
exit 0
""",
    )
    with pytest.raises(ValueError, match="surviving process group"):
        module._run_bounded(
            [str(orphan)],
            environment={"PATH": "/usr/bin:/bin"},
            deadline=5,
        )
    orphan_pid = int(pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.kill(orphan_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        pytest.fail("successful child left an unfenced process-group descendant")


def test_atomic_bundle_installer_and_descriptor_wiring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_bundle_installer()
    etc = tmp_path / "etc"
    etc.mkdir(mode=0o755)
    omega = etc / "omega"
    omega.mkdir(mode=0o755)
    host_identity = omega / "gcp-host-identity.json"
    host_identity.write_bytes(module._canonical_json(module.EXPECTED_HOST_IDENTITY))
    host_identity.chmod(0o400)
    secret_authority = omega / "ghcr-pull-secret-authority.json"
    monkeypatch.setattr(module, "HOST_IDENTITY_PATH", host_identity)
    monkeypatch.setattr(module, "SECRET_AUTHORITY_PATH", secret_authority)
    source_sha = "a" * 40
    release_root = tmp_path / "releases" / source_sha
    release_root.mkdir(parents=True)
    for _name, (relative, _mode) in module.BUNDLE_FILES.items():
        source = ROOT / relative
        destination = release_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        destination.chmod(source.stat().st_mode & 0o777)
    manifest_destination = release_root / module.SOURCE_MANIFEST
    manifest_destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(BUNDLE_SOURCE_MANIFEST, manifest_destination)
    manifest_destination.chmod(0o400)

    bundle_root = tmp_path / "ghcr-release-bundles"
    bundle_root.mkdir()
    destination = module.install_bundle(
        release_root,
        source_sha,
        bundle_root=bundle_root,
        uid=os.geteuid(),
        gid=os.getegid(),
    )
    assert destination == bundle_root / source_sha
    assert destination.stat().st_mode & 0o777 == 0o500
    assert {item.name for item in destination.iterdir()} == {
        *module.BUNDLE_FILES,
        "bundle-manifest.json",
    }
    assert secret_authority.read_bytes() == module._canonical_json(
        module.SECRET_AUTHORITY
    )
    assert stat.S_IMODE(secret_authority.stat().st_mode) == 0o400
    authority_identity = (
        secret_authority.stat().st_dev,
        secret_authority.stat().st_ino,
    )
    assert (
        module.install_bundle(
            release_root,
            source_sha,
            bundle_root=bundle_root,
            uid=os.geteuid(),
            gid=os.getegid(),
        )
        == destination
    )
    assert (secret_authority.stat().st_dev, secret_authority.stat().st_ino) == (
        authority_identity
    )
    validation = subprocess.run(
        [
            sys.executable,
            "-I",
            str(destination / "secure-ghcr-session.py"),
            "validate-bundle",
            "--bundle-dir",
            str(destination),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    assert validation.returncode == 0, validation.stderr

    destination.chmod(0o700)
    tampered = destination / "validate-lock-output.py"
    tampered.chmod(0o600)
    tampered.write_bytes(tampered.read_bytes() + b"\n")
    tampered.chmod(0o400)
    destination.chmod(0o500)
    rejected = subprocess.run(
        [
            sys.executable,
            "-I",
            str(destination / "secure-ghcr-session.py"),
            "validate-bundle",
            "--bundle-dir",
            str(destination),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    assert rejected.returncode == 6
    assert "digest differs" in rejected.stderr


def test_bundle_installer_rejects_host_or_existing_secret_authority_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_bundle_installer()
    etc = tmp_path / "etc"
    etc.mkdir(mode=0o755)
    omega = etc / "omega"
    omega.mkdir(mode=0o755)
    identity = omega / "gcp-host-identity.json"
    authority = omega / "ghcr-pull-secret-authority.json"
    monkeypatch.setattr(module, "HOST_IDENTITY_PATH", identity)
    monkeypatch.setattr(module, "SECRET_AUTHORITY_PATH", authority)

    mismatched = {**module.EXPECTED_HOST_IDENTITY, "instance_id": "1"}
    identity.write_bytes(module._canonical_json(mismatched))
    identity.chmod(0o400)
    with pytest.raises(ValueError, match="host identity differs"):
        module._install_secret_authority(uid=os.geteuid(), gid=os.getegid())
    assert not authority.exists()

    identity.chmod(0o600)
    identity.write_bytes(module._canonical_json(module.EXPECTED_HOST_IDENTITY))
    identity.chmod(0o400)
    authority.write_text("{}\n", encoding="utf-8")
    authority.chmod(0o400)
    with pytest.raises(ValueError, match="existing GHCR secret authority conflicts"):
        module._install_secret_authority(uid=os.geteuid(), gid=os.getegid())


@pytest.mark.parametrize(
    ("authority_mode", "image_tag"),
    [
        ("candidate", f"candidate-{'b' * 40}"),
        ("published", "v1.45.207-beta"),
        ("legacy-rollback", "v1.45.207-beta"),
    ],
)
def test_image_preflight_contract_uses_one_canonical_staging_shape(
    tmp_path: Path, authority_mode: str, image_tag: str
) -> None:
    module = _load_lock_validator()
    revision = "b" * 40
    lock_file = _new_test_lock_file(tmp_path, revision)
    lock_root = tmp_path / "image-locks"
    module.validate_lock_output(
        lock_file,
        revision,
        authority_mode=authority_mode,
        image_tag=image_tag,
        version="1.45.207-beta",
        lock_root=lock_root,
        managed_parents=(lock_root,),
        uid=os.geteuid(),
        gid=os.getegid(),
    )
    contract = _read(BUNDLE_CONTRACT)
    assert "/image-locks/.<target-sha>.tmp.<pid>/release-images.env" in contract
    assert "candidate, published, and legacy-rollback" in contract


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
    with pytest.raises(ValueError, match="staging file is unsafe"):
        module.validate_lock_output(lock_file, revision, **options)
    lock_file.unlink()

    authority_file = Path(f"{lock_file}.authority.json")
    authority_file.write_text("{}\n", encoding="utf-8")
    authority_file.chmod(0o600)
    module.validate_lock_output(lock_file, revision, **options)
    lock_file.write_text("safe partial\n", encoding="utf-8")
    lock_file.chmod(0o600)
    module.validate_lock_output(lock_file, revision, **options)
    commit_file = Path(f"{lock_file}.commit.json")
    commit_file.write_text("{}\n", encoding="utf-8")
    commit_file.chmod(0o400)
    module.validate_lock_output(lock_file, revision, **options)
    Path(f"{authority_file}.pending").write_text("unsafe\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unsafe|ambiguous|structurally incomplete"):
        module.validate_lock_output(lock_file, revision, **options)

    final_dir = lock_root / revision
    final_dir.mkdir(mode=0o700)
    with pytest.raises(ValueError, match="must remain.*staging"):
        module.validate_lock_output(
            final_dir / "release-images.env", revision, **options
        )

    source = _read(GCP_TERRAFORM / "release/publish-image-lock.py")
    assert source.index("os.fsync(descriptor)") < source.index("os.link(")
    assert source.index("os.link(") < source.index(
        "os.fsync(self.directory_fd)", source.index("os.link(")
    )
    assert '"commit": f"{args.destination_lock.name}.commit.json"' in source
    assert "authority_sha256" in source
    assert "lock_sha256" in source
    assert 'mv -f -- "$temp_lock"' not in source


def _publisher_command(
    source_lock: Path,
    source_authority: Path,
    destination_lock: Path,
) -> list[str]:
    return [
        sys.executable,
        "-I",
        str(LOCK_PUBLISHER),
        "--source-lock",
        str(source_lock),
        "--source-authority",
        str(source_authority),
        "--destination-lock",
        str(destination_lock),
        "--source-sha",
        "b" * 40,
        "--version",
        "1.45.207-beta",
        "--image-tag",
        "candidate-" + "b" * 40,
        "--authority-mode",
        "candidate",
    ]


@pytest.mark.parametrize("artifact", ["authority", "lock", "commit"])
@pytest.mark.parametrize(
    "phase",
    [
        "pending-file-fsync",
        "pending-dir-fsync",
        "link",
        "link-dir-fsync",
        "unlink",
        "unlink-dir-fsync",
    ],
)
def test_image_lock_publication_recovers_after_sigkill_at_every_phase(
    tmp_path: Path, artifact: str, phase: str
) -> None:
    sources = tmp_path / "sources"
    sources.mkdir(mode=0o700)
    source_lock = sources / "release-images.env"
    source_authority = sources / "release-images.env.authority.json"
    source_lock.write_text("console=ghcr.io/example@sha256:" + "a" * 64 + "\n")
    source_authority.write_text('{"schema_version":1}\n')
    source_lock.chmod(0o600)
    source_authority.chmod(0o600)
    destination = tmp_path / "image-locks" / f"{artifact}-{phase}"
    destination.mkdir(parents=True, mode=0o700)
    destination_lock = destination / "release-images.env"
    command = _publisher_command(source_lock, source_authority, destination_lock)

    crashed = subprocess.run(
        command,
        cwd=ROOT,
        env={
            **os.environ,
            "OMEGA_IMAGE_LOCK_TESTING": "1",
            "OMEGA_IMAGE_LOCK_CRASH_AT": f"{artifact}:{phase}",
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert crashed.returncode == -signal.SIGKILL

    recovered = subprocess.run(
        command,
        cwd=ROOT,
        env={
            name: value
            for name, value in os.environ.items()
            if name not in {"OMEGA_IMAGE_LOCK_TESTING", "OMEGA_IMAGE_LOCK_CRASH_AT"}
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert recovered.returncode == 0, recovered.stderr
    assert destination_lock.read_bytes() == source_lock.read_bytes()
    assert Path(f"{destination_lock}.authority.json").read_bytes() == (
        source_authority.read_bytes()
    )
    assert (
        json.loads(Path(f"{destination_lock}.commit.json").read_bytes())["lock_sha256"]
        == "sha256:" + hashlib.sha256(source_lock.read_bytes()).hexdigest()
    )
    assert not list(destination.glob("*.pending"))


def test_lock_validator_accepts_only_same_inode_interrupted_link_state(
    tmp_path: Path,
) -> None:
    module = _load_lock_validator()
    revision = "b" * 40
    lock_file = _new_test_lock_file(tmp_path, revision)
    authority = Path(f"{lock_file}.authority.json")
    pending = Path(f"{authority}.pending")
    authority.write_text("{}\n", encoding="utf-8")
    authority.chmod(0o600)
    os.link(authority, pending)
    options = {
        "lock_root": tmp_path / "image-locks",
        "managed_parents": (tmp_path / "image-locks",),
        "uid": os.geteuid(),
        "gid": os.getegid(),
    }

    module.validate_lock_output(lock_file, revision, **options)
    pending.unlink()
    pending.write_text("{}\n", encoding="utf-8")
    pending.chmod(0o600)
    with pytest.raises(ValueError, match="ambiguous"):
        module.validate_lock_output(lock_file, revision, **options)


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
        "tag_proof_sha256": f"sha256:{'e' * 64}",
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


def test_authority_digest_output_rejects_links_before_truncation(
    tmp_path: Path,
) -> None:
    spec = importlib.util.spec_from_file_location(
        "gcp_authority_validator_test", AUTHORITY_VALIDATOR
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    victim = tmp_path / "victim"
    victim.write_bytes(b"do-not-truncate")
    victim.chmod(0o600)
    linked = tmp_path / "digests"
    os.link(victim, linked)

    with pytest.raises(ValueError, match="unsafe"):
        module._write_digests(linked, b"replacement")
    assert victim.read_bytes() == b"do-not-truncate"


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


@pytest.mark.parametrize(
    "swap",
    [
        "none",
        "parent-root",
        "directory",
        "config",
        "context",
        "context-secret-missing",
        "context-secret-malformed",
        "lock",
    ],
)
def test_registry_consumer_rejects_every_stable_auth_descriptor_name_swap(
    tmp_path: Path, swap: str
) -> None:
    tmp_path.chmod(0o700)
    root = tmp_path / "omega-gcp-ghcr-auth"
    root.mkdir(mode=0o700)
    directory = root / "omega-gcp-ghcr-auth.ABC123"
    directory.mkdir(mode=0o700)
    auth = base64.b64encode(b"release-reader:server-owned-token-12345").decode("ascii")
    config = directory / "config.json"
    config.write_text(
        json.dumps({"auths": {"ghcr.io": {"auth": auth}}}, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    config.chmod(0o600)
    context = directory / "auth-context.json"
    context.write_text(
        json.dumps(
            {
                "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
                "owner": "emmanuelnavaromero02-commits",
                "private_packages": ["banxico", "inegi", "sec_edgar"],
                "registry": "ghcr.io",
                "schema_version": 1,
                "secret_version_resource": (
                    "projects/omega-test-project/secrets/"
                    "omega-staging-ghcr_pull_credentials/versions/7"
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    context.chmod(0o400)
    lock = tmp_path / ".omega-gcp-ghcr-release.lock"
    lock.write_bytes(b"")
    lock.chmod(0o600)

    harness = tmp_path / "stable-auth-consumer.py"
    _write_executable(
        harness,
        f"""#!/usr/bin/env python3
import fcntl
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, {str(ROOT)!r})
from scripts import release_images

parent = Path(sys.argv[1])
swap = sys.argv[2]
root = parent / "omega-gcp-ghcr-auth"
directory = root / "omega-gcp-ghcr-auth.ABC123"
config = directory / "config.json"
context = directory / "auth-context.json"
lock = parent / ".omega-gcp-ghcr-release.lock"
sources = (
    os.open(parent, os.O_RDONLY | os.O_DIRECTORY),
    os.open(root, os.O_RDONLY | os.O_DIRECTORY),
    os.open(context, os.O_RDONLY),
    os.open(config, os.O_RDONLY),
    os.open(directory, os.O_RDONLY | os.O_DIRECTORY),
    os.open(lock, os.O_RDWR),
)
duplicates = [
    fcntl.fcntl(fd, getattr(fcntl, "F_DUPFD_CLOEXEC", fcntl.F_DUPFD), 10)
    for fd in sources
]
try:
    for fd, target in zip(duplicates, range(4, 10), strict=True):
        os.dup2(fd, target, inheritable=True)
finally:
    for fd in duplicates:
        os.close(fd)

if swap == "parent-root":
    root.rename(parent / "captured-root")
    root.mkdir(mode=0o700)
elif swap == "directory":
    directory.rename(root / "captured-directory")
    directory.mkdir(mode=0o700)
elif swap == "config":
    raw = config.read_bytes()
    config.rename(directory / "captured-config.json")
    config.write_bytes(raw)
    config.chmod(0o600)
elif swap == "context":
    raw = context.read_bytes()
    context.rename(directory / "captured-context.json")
    context.write_bytes(raw)
    context.chmod(0o400)
elif swap in {{"context-secret-missing", "context-secret-malformed"}}:
    value = json.loads(context.read_bytes())
    if swap == "context-secret-missing":
        del value["secret_version_resource"]
    else:
        value["secret_version_resource"] = "projects/attacker/secrets/other/versions/1"
    context.chmod(0o600)
    context.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\\n",
        encoding="utf-8",
    )
    context.chmod(0o400)
elif swap == "lock":
    lock.rename(parent / "captured-lock")
    lock.write_bytes(b"")
    lock.chmod(0o600)

os.environ.update({{
    "OMEGA_GHCR_AUTH_PARENT_FD": "4",
    "OMEGA_GHCR_AUTH_ROOT_FD": "5",
    "OMEGA_GHCR_AUTH_ROOT_NAME": "omega-gcp-ghcr-auth",
    "OMEGA_GHCR_AUTH_CONTEXT_FD": "6",
    "OMEGA_GHCR_AUTH_CONFIG_FD": "7",
    "OMEGA_GHCR_AUTH_DIRECTORY_FD": "8",
    "OMEGA_GHCR_AUTH_DIRECTORY_NAME": "omega-gcp-ghcr-auth.ABC123",
    "OMEGA_GHCR_AUTH_LOCK_FD": "9",
}})
release_images.GCP_GHCR_AUTH_OWNER_UID = os.geteuid()
release_images.GCP_GHCR_AUTH_OWNER_GID = os.getegid()
try:
    client = release_images.RegistryClient.from_private_auth_file(
        Path("/proc/self/fd/8/config.json")
    )
except release_images.ReleaseImageError:
    if swap == "none":
        raise
    raise SystemExit(0)
if swap != "none" or client._actor != "release-reader":
    raise SystemExit(1)
""",
    )
    result = subprocess.run(
        [sys.executable, "-I", str(harness), str(tmp_path), swap],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stderr


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
        "versions/${secret_version_alias}:access",
        '"$MKTEMP_BIN" -d',
        "DOCKER_CONFIG",
        "set +x",
        "export PATH=/usr/sbin:/usr/bin:/sbin:/bin",
        'docker_socket="/run/docker.sock"',
        'export DOCKER_HOST="unix://${docker_socket}"',
        "not stat.S_ISSOCK",
        "readonly CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt",
        '--cacert "$CA_BUNDLE"',
        'auth_lock="${auth_root%/*}/${AUTH_LOCK_NAME}"',
    ):
        assert marker in runner

    assert "versions/latest" not in runner
    assert "gcloud " not in runner
    assert '"$GHCR_TOKEN"' not in runner
    assert "GHCR_TOKEN=" not in runner
    assert "unset GHCR_TOKEN GITHUB_TOKEN" in runner
    assert "github.token" not in runner
    assert "OMEGA_GHCR_AUTH_TEST" not in runner
    assert "controller-attestation" not in runner
    assert runner.startswith("#!/bin/bash -p\n")
    assert 'if [[ "${EUID:-$("$ID_BIN" -u)}" -ne 0 ]]' in runner
    assert "SSLKEYLOGFILE" in runner
    assert "BASH_FUNC_" in runner
    assert "secure-ghcr-session.py" in runner
    assert "auth-exec" in runner
    assert "--expected-directory-identity" in runner
    assert 'auth_root_fd_path="/proc/self/fd/${AUTH_ROOT_FD}"' in runner
    assert "readonly HOST_IDENTITY=/etc/omega/gcp-host-identity.json" in runner
    assert "readonly GCP_ENVIRONMENT=staging" in runner
    assert "unset OMEGA_GCP_ENVIRONMENT" in runner
    assert 'gcp_environment="$GCP_ENVIRONMENT"' in runner
    assert "${OMEGA_GCP_ENVIRONMENT" not in runner
    startup = _read(GCP_TERRAFORM / "templates/startup.sh.tftpl")
    assert "/etc/omega/gcp-host-identity.json" in startup
    assert "metadata_identity_value" not in startup
    assert "HOST_IDENTITY_TMP" not in startup
    assert "provisioned GCP host identity is unavailable" in startup
    session_helper = _read(GCP_TERRAFORM / "release/secure-ghcr-session.py")
    assert '"--password-stdin"' in session_helper
    assert 'command = [os.fspath(docker), "logout", "ghcr.io"]' in session_helper
    assert "argparse.REMAINDER" not in session_helper
    assert "PREFLIGHT_DEADLINE = 2 * 60 * 60" in session_helper
    assert "start_new_session=True" in session_helper
    assert "os.killpg(process.pid, signal.SIGTERM)" in session_helper
    assert "os.killpg(process.pid, signal.SIGKILL)" in session_helper
    for marker in (
        "PARENT_FD = 4",
        "ROOT_FD = 5",
        "LOCK_FD = 9",
        "os.O_RDWR",
        "os.O_CREAT",
        'getattr(os, "O_CLOEXEC", 0)',
        'getattr(os, "O_NOFOLLOW", 0)',
        "dir_fd=parent_fd",
    ):
        assert marker in session_helper
    assert "os.O_TRUNC" not in session_helper
    assert "exec 4>&- 9>&-" in runner
    preflight = _read(PREFLIGHT)
    environment_names = set(re.findall(r"\bOMEGA_[A-Z0-9_]+", preflight))
    assert not {
        name for name in environment_names if re.search(r"(?:^|_)TEST(?:_|$)", name)
    }
    assert "TEST_AUTHORITY_FILE" not in preflight
    assert 'docker_root="/var/lib/docker"' in preflight
    assert "OMEGA_GHCR_AUTH_LOCK_FD" in preflight
    assert "OMEGA_GHCR_AUTH_PARENT_FD" in preflight
    assert "fcntl.LOCK_EX | fcntl.LOCK_NB" in preflight
    assert "bounded_docker info" in preflight
    assert '"{{.DockerRootDir}}|{{.ID}}"' in session_helper
    assert 'bounded_docker pull --reference "$reference"' in preflight
    assert "bounded_docker inspect-repo-digests" in preflight
    assert "bounded_docker inspect-oci-identity" in preflight
    assert "bounded_docker inspect-image-id" in preflight
    assert 'with_docker_config_fd "$DOCKER_BIN"' not in preflight
    assert (
        'if ! with_auth_verifier_fds "$PYTHON_BIN" -I "$release_helper"'
        ' "${authority_args[@]}"' in preflight
    )
    assert "secret_version" not in _read(GCP_TERRAFORM / "variables.tf")
    assert "DOCKER_CONFIG" not in _read(
        GCP_TERRAFORM / "templates/docker-compose.gcp.yml.tftpl"
    )


def test_bundle_and_server_side_secret_contract_is_complete() -> None:
    runner = _read(AUTH_RUNNER)
    helper = _read(SESSION_HELPER)
    preflight = _read(PREFLIGHT)
    contract = _read(BUNDLE_CONTRACT)
    assert "versions/${secret_version_alias}:access" in runner
    assert 'value.get("secret_version_alias") != "active"' in runner
    assert "versions/latest" not in runner
    assert "resolved_secret_version_resource" in runner
    assert "requested_secret_version" in runner
    assert '"$requested_secret_version" != "$resolved_secret_version"' in runner
    assert "/etc/omega/ghcr-pull-secret-authority.json" in runner
    assert "PR4" not in contract
    assert "never rename either entrypoint" in contract
    assert "OMEGA_RELEASE_ANNOTATED_TAG_OBJECT_FILE" in contract
    assert (
        "/opt/modecissions/shared/release-authority/<target-sha>/annotated-tag.object"
        in contract
    )
    for name, descriptor in {
        "ghcr-auth-run.sh": 11,
        "secure-ghcr-session.py": 12,
        "preflight-release-images.sh": 13,
        "validate-image-authority.py": 14,
        "validate-lock-output.py": 15,
        "publish-image-lock.py": 16,
        "release_images.py": 17,
    }.items():
        assert f'"{name}": {descriptor}' in helper
    assert "docker_fd, docker = _open_executable" in helper
    assert 'Path(f"/proc/self/fd/{descriptor}")' in helper
    assert "OMEGA_GHCR_BUNDLE_RELEASE_IMAGES_FD" in preflight
    manifest = json.loads(BUNDLE_SOURCE_MANIFEST.read_bytes())
    installer = _load_bundle_installer()
    assert set(manifest) == {"files", "schema_version"}
    assert manifest["schema_version"] == 1
    assert set(manifest["files"]) == set(installer.BUNDLE_FILES)
    for name, (source_path, mode) in installer.BUNDLE_FILES.items():
        raw = (ROOT / source_path).read_bytes()
        assert manifest["files"][name] == {
            "mode": f"{mode:04o}",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "source": source_path,
        }
    lint = _read(ROOT / ".github/workflows/lint.yml")
    security = _read(ROOT / ".github/workflows/security.yml")
    for workflow in (lint, security):
        assert "infra/terraform-gcp/release/install-ghcr-release-bundle.py" in workflow
    assert "infra/terraform-gcp/release/ghcr-release-bundle.manifest.json" in lint
    assert "Verify sealed GHCR release bundle inventory" in lint


def test_authenticated_curl_paths_disable_environment_proxies() -> None:
    runner = _read(AUTH_RUNNER)
    commands = (
        re.search(
            r'"\$CURL_BIN" (?:(?!"\$CURL_BIN").)*?--config "\$curl_config" "\$secret_url"',
            runner,
            re.DOTALL,
        ),
        re.search(
            r'"\$CURL_BIN" (?:(?!"\$CURL_BIN").)*?--config "\$github_config"'
            r'(?:(?!"\$CURL_BIN").)*?api\.github\.com/',
            runner,
            re.DOTALL,
        ),
    )
    assert all(command is not None for command in commands)
    for command in commands:
        assert command is not None
        assert command.group(0).startswith('"$CURL_BIN" -q ')
        assert command.group(0).count("--noproxy '*'") == 1
        assert command.group(0).count('--cacert "$CA_BUNDLE"') == 1
    assert runner.count('"$CURL_BIN" -q ') == 3
    assert "curl --fail" not in runner


@pytest.mark.parametrize(
    ("failure_target", "expected_code"),
    [("metadata", 6), ("secret", 8), ("github", 10)],
)
def test_auth_runner_rejects_non_200_http_before_docker_auth(
    tmp_path: Path, failure_target: str, expected_code: int
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    credential = json.dumps(
        {"username": "release-reader", "token": "server-owned-token-12345"},
        separators=(",", ":"),
    ).encode()
    encoded = base64.b64encode(credential).decode()
    checksum = _crc32c(credential)
    docker_marker = tmp_path / "docker-ran"
    _write_executable(
        fake_bin / "curl",
        f"""#!/usr/bin/env bash
set -euo pipefail
args="$*"
if [[ "$args" == *"project/project-id"* ]]; then
  printf '%s' omega-test-project
elif [[ "$args" == *"service-accounts/default/token"* ]]; then
  printf '%s' '{{"access_token":"metadata-access-token","expires_in":3599,"token_type":"Bearer"}}'
elif [[ "$args" == *"versions/active:access"* ]]; then
  printf '%s' '{{"name":"projects/omega-test-project/secrets/omega-staging-ghcr_pull_credentials/versions/7","payload":{{"data":"{encoded}","dataCrc32c":"{checksum}"}}}}'
elif [[ "$args" == *"api.github.com/"* ]]; then
  previous=""
  header=""
  output=""
  for argument in "$@"; do
    [[ "$previous" == "--dump-header" ]] && header="$argument"
    [[ "$previous" == "--output" ]] && output="$argument"
    previous="$argument"
  done
  package="${{@: -1}}"
  package="${{package##*/}}"
  printf 'HTTP/1.1 200 OK\r\nX-OAuth-Scopes: read:packages\r\n\r\n' > "$header"
  printf '{{"id":17,"name":"%s","package_type":"container","visibility":"private"}}' "$package" > "$output"
else
  exit 91
fi
""",
    )
    _write_executable(
        fake_bin / "docker",
        f"#!/bin/sh\ntouch {shlex.quote(str(docker_marker))}\nexit 99\n",
    )
    result = subprocess.run(
        _auth_runner_command(_temporary_auth_runner(tmp_path), "-c", "true"),
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "OMEGA_GCP_ENVIRONMENT": "staging",
            "OMEGA_GHCR_PULL_SECRET_VERSION": "7",
            "TEST_HTTP_FAILURE_TARGET": failure_target,
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == expected_code
    assert not docker_marker.exists()


def test_auth_runner_parent_lock_rejects_root_swap_and_concurrent_release_before_network(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    first_curl_started = tmp_path / "first-curl-started"
    release_first = tmp_path / "release-first-curl"
    second_curl = tmp_path / "unexpected-second-curl"
    _write_executable(
        fake_bin / "curl",
        f"""#!/bin/sh
if [ -e {shlex.quote(str(first_curl_started))} ]; then
  touch {shlex.quote(str(second_curl))}
  exit 97
fi
touch {shlex.quote(str(first_curl_started))}
while [ ! -e {shlex.quote(str(release_first))} ]; do sleep 0.02; done
exit 96
""",
    )
    runner = _temporary_auth_runner(tmp_path)
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "OMEGA_GCP_ENVIRONMENT": "staging",
        "OMEGA_GHCR_PULL_SECRET_VERSION": "7",
    }
    first = subprocess.Popen(
        _auth_runner_command(runner, "-c", "true"),
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    first_stdout = ""
    first_stderr = ""
    try:
        deadline = time.monotonic() + 20
        while (
            not first_curl_started.exists()
            and first.poll() is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        assert first_curl_started.exists()

        # A root-level concurrent process may replace the entire auth-root
        # pathname. The one host lock is anchored in the already-open parent,
        # so this must not create a second lock namespace.
        auth_root = tmp_path / "auth-root"
        captured_root = tmp_path / "captured-auth-root"
        auth_root.rename(captured_root)
        auth_root.mkdir(mode=0o700)

        second = subprocess.run(
            _auth_runner_command(runner, "-c", "true"),
            cwd=ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=5,
        )
        assert second.returncode == 6
        assert "another canonical GHCR release operation holds the host lock" in (
            second.stderr
        )
        assert not second_curl.exists()
        assert not any(auth_root.iterdir())
    finally:
        release_first.touch()
        first_stdout, first_stderr = first.communicate(timeout=20)
    assert first.returncode != 0
    assert "server-owned-token" not in first_stdout
    assert "server-owned-token" not in first_stderr


@pytest.mark.parametrize("target", ["auth", "preflight"])
def test_privileged_shell_boundary_rejects_exported_functions_before_network(
    tmp_path: Path, target: str
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    markers = {
        name: tmp_path / f"{name}-ran"
        for name in ("bash", "python3", "curl", "docker", "bash-env")
    }
    for name in ("bash", "python3", "curl", "docker"):
        _write_executable(
            fake_bin / name,
            f"#!/bin/sh\ntouch {shlex.quote(str(markers[name]))}\nexit 99\n",
        )
    bash_env = tmp_path / "hostile-bash-env.sh"
    bash_env.write_text(
        f"touch {shlex.quote(str(markers['bash-env']))}\n", encoding="utf-8"
    )
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "BASH_ENV": str(bash_env),
        "ENV": str(bash_env),
        "BASH_FUNC_python3%%": "() { touch hostile-python-function; }",
        "BASH_FUNC_curl%%": "() { touch hostile-curl-function; }",
        "BASH_FUNC_docker%%": "() { touch hostile-docker-function; }",
    }
    if target == "auth":
        runner = _temporary_auth_runner(tmp_path)
        command = _auth_runner_command(runner, "-c", "true")
    else:
        docker_root = tmp_path / "docker-root"
        docker_root.mkdir()
        preflight = _temporary_preflight(tmp_path, docker_root=docker_root)
        command = _preflight_command(
            preflight,
            "emmanuelnavaromero02-commits",
            f"candidate-{'b' * 40}",
            "b" * 40,
            "1.45.207-beta",
            str(tmp_path / "lock.env"),
        )

    result = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 2
    assert "exported shell functions are forbidden" in result.stderr
    assert not any(path.exists() for path in markers.values())


def test_curl_q_first_ignores_hostile_curlrc_without_leaking_header(
    tmp_path: Path,
) -> None:
    curl = shutil.which("curl")
    assert curl is not None
    captured: list[str | None] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            captured.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    hostile = "Bearer hostile-curlrc-secret-material"
    curl_home = tmp_path / "curl-home"
    curl_home.mkdir()
    (curl_home / ".curlrc").write_text(
        f'header = "Authorization: {hostile}"\n', encoding="utf-8"
    )
    url = f"http://127.0.0.1:{server.server_port}/"
    environment = {**os.environ, "HOME": str(curl_home), "CURL_HOME": str(curl_home)}
    try:
        control = subprocess.run(
            [curl, "--silent", "--show-error", url],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        protected = subprocess.run(
            [curl, "-q", "--silent", "--show-error", url],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
    assert control.returncode == protected.returncode == 0
    assert captured == [hostile, None]
    assert hostile not in protected.stdout
    assert hostile not in protected.stderr


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
test "${{1:-}}" = "-q"
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
elif [[ "$args" == *"versions/active:access"* ]]; then
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
[[ "${{DOCKER_HOST:-}}" == unix://*/docker.sock ]]
test -z "${{DOCKER_CONTEXT+x}}"
test -z "${{DOCKER_TLS_VERIFY+x}}"
test -z "${{DOCKER_CERT_PATH+x}}"
test -z "${{DOCKER_DEFAULT_PLATFORM+x}}"
test -z "${{COMPOSE_FILE+x}}"
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
        _auth_runner_command(
            auth_runner,
            "-c",
            'set -euo pipefail; test "$OMEGA_GHCR_AUTH_ACTIVE" = 1; '
            'test "$OMEGA_GHCR_PRIVATE_PACKAGES_VERIFIED" = 1; '
            'test -z "${GHCR_TOKEN+x}"; test -z "${GITHUB_TOKEN+x}"; '
            'test -z "${PYTHONPATH+x}"; test -z "${PYTHONHOME+x}"; '
            'test -z "${SSL_CERT_FILE+x}"; test -z "${CURL_CA_BUNDLE+x}"; '
            'test "$OMEGA_GHCR_AUTH_LOCK_FD" = 9; test -e /dev/fd/9; '
            '[[ "$DOCKER_HOST" == unix://*/docker.sock ]]; '
            'test -n "$TEST_CONTEXT_RECORD"; '
            'cp /dev/fd/6 "$TEST_CONTEXT_RECORD"; '
            'test -s /dev/fd/7; printf "%s\\n" command-ok',
        ),
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
            "DOCKER_HOST": "tcp://attacker.invalid:2376",
            "DOCKER_CONTEXT": "attacker-context",
            "DOCKER_TLS_VERIFY": "1",
            "DOCKER_CERT_PATH": str(tmp_path / "attacker-certs"),
            "DOCKER_DEFAULT_PLATFORM": "linux/attacker",
            "COMPOSE_FILE": str(tmp_path / "attacker-compose.yml"),
            "PYTHONPATH": str(tmp_path / "attacker-python"),
            "PYTHONHOME": str(tmp_path / "attacker-python-home"),
            "SSL_CERT_FILE": str(tmp_path / "attacker-ca.pem"),
            "CURL_CA_BUNDLE": str(tmp_path / "attacker-ca.pem"),
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
    curl_argv = curl_argv_record.read_text(encoding="utf-8")
    request_count = 10 if expected_code == 0 else 8
    assert curl_argv.count("-q --fail") == request_count
    assert curl_argv.count("--noproxy *") == request_count
    assert curl_argv.count("--write-out") == request_count
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
        "secret_version_resource",
    }
    assert context["owner"] == "emmanuelnavaromero02-commits"
    assert context["registry"] == "ghcr.io"
    assert context["private_packages"] == ["banxico", "inegi", "sec_edgar"]
    assert context["secret_version_resource"].endswith("/versions/7")
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
        _auth_runner_command(auth_runner, "-c", "true"),
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


@pytest.mark.parametrize(
    "failure",
    [
        "metadata-mismatch",
        "duplicate-key",
        "noncanonical",
        "extra-field",
        "boolean-schema",
        "wrong-mode",
        "symlink",
    ],
)
def test_auth_runner_rejects_untrusted_host_identity_before_token_or_secret(
    tmp_path: Path, failure: str
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    token_marker = tmp_path / "metadata-token-contacted"
    secret_marker = tmp_path / "secret-manager-contacted"
    _write_executable(
        fake_bin / "curl",
        f"""#!/usr/bin/env bash
set -euo pipefail
case "$*" in
  *project/project-id*) printf '%s' omega-test-project ;;
  *service-accounts/default/token*)
    touch {shlex.quote(str(token_marker))}
    printf '%s' '{{"access_token":"metadata-access-token","expires_in":3599,"token_type":"Bearer"}}'
    ;;
  *secretmanager.googleapis.com*) touch {shlex.quote(str(secret_marker))} ;;
  *) exit 91 ;;
esac
""",
    )
    _write_executable(fake_bin / "docker", "#!/bin/sh\nexit 99\n")
    runner = _temporary_auth_runner(tmp_path)
    identity = tmp_path / "gcp-host-identity.json"
    value = json.loads(identity.read_text(encoding="utf-8"))
    if failure not in {"wrong-mode", "symlink"}:
        identity.chmod(0o600)
    if failure == "metadata-mismatch":
        value["instance_name"] = "omega-attacker-app"
    elif failure == "extra-field":
        value["environment"] = "staging"
    elif failure == "boolean-schema":
        value["schema_version"] = True
    if failure in {"metadata-mismatch", "extra-field", "boolean-schema"}:
        identity.write_text(
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    elif failure == "duplicate-key":
        canonical = identity.read_text(encoding="utf-8")
        identity.write_text(
            canonical.replace(
                '"schema_version":1,',
                '"schema_version":1,"schema_version":1,',
            ),
            encoding="utf-8",
        )
    elif failure == "noncanonical":
        identity.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    elif failure == "wrong-mode":
        identity.chmod(0o600)
    elif failure == "symlink":
        replacement = tmp_path / "attacker-host-identity.json"
        replacement.write_bytes(identity.read_bytes())
        replacement.chmod(0o400)
        identity.unlink()
        identity.symlink_to(replacement)
    if failure not in {"wrong-mode", "symlink"}:
        identity.chmod(0o400)

    result = subprocess.run(
        _auth_runner_command(runner),
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "OMEGA_GCP_ENVIRONMENT": "production",
            "OMEGA_GHCR_PULL_SECRET_VERSION": "7",
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 6
    assert "host identity does not match metadata" in result.stderr
    assert not token_marker.exists()
    assert not secret_marker.exists()


def test_auth_runner_rejects_old_arbitrary_command_contract_before_network(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    network_marker = tmp_path / "network-contacted"
    command_marker = tmp_path / "arbitrary-command-ran"
    _write_executable(
        fake_bin / "curl",
        f"#!/bin/sh\ntouch {shlex.quote(str(network_marker))}\nexit 99\n",
    )
    _write_executable(fake_bin / "docker", "#!/bin/sh\nexit 99\n")
    runner = _temporary_auth_runner(tmp_path)
    result = subprocess.run(
        [
            "/bin/bash",
            "-p",
            str(runner),
            "/bin/bash",
            "-p",
            "-c",
            f"touch {shlex.quote(str(command_marker))}",
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "OMEGA_GHCR_PULL_SECRET_VERSION": "7",
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 2
    assert "exact sibling preflight and five authority arguments" in result.stderr
    assert not command_marker.exists()
    assert not network_marker.exists()


def test_auth_runner_rejects_symlinked_local_docker_socket_before_network(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    curl_marker = tmp_path / "curl-ran"
    _write_executable(
        fake_bin / "curl",
        f"#!/bin/sh\ntouch {shlex.quote(str(curl_marker))}\nexit 99\n",
    )
    auth_runner = _temporary_auth_runner(tmp_path)
    docker_socket = tmp_path / "docker.sock"
    docker_socket.unlink()
    docker_socket.symlink_to(tmp_path / "attacker.sock")

    result = subprocess.run(
        _auth_runner_command(auth_runner, "-c", "true"),
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "OMEGA_GCP_ENVIRONMENT": "staging",
            "OMEGA_GHCR_PULL_SECRET_VERSION": "7",
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 6
    assert "local root-owned Docker socket" in result.stderr
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
        _auth_runner_command(
            auth_runner, "-c", f"touch {shlex.quote(str(command_marker))}"
        ),
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
        _auth_runner_command(_temporary_auth_runner(tmp_path), "-c", "true"),
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
  *versions/active:access*) cat "$TEST_SECRET_FILE" ;;
  *) exit 91 ;;
esac
""",
    )
    _write_executable(
        fake_bin / "docker",
        f"#!/bin/sh\ntouch {shlex.quote(str(docker_marker))}\nexit 99\n",
    )
    result = subprocess.run(
        _auth_runner_command(_temporary_auth_runner(tmp_path), "-c", "true"),
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
    command = _preflight_command(
        preflight,
        "emmanuelnavaromero02-commits",
        f"candidate-{'b' * 40}",
        "b" * 40,
        "1.45.207-beta",
        str(tmp_path / "lock.env"),
    )
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DOCKER_CONFIG": str(docker_config),
        "DOCKER_HOST": "unix:///run/docker.sock",
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
    docker_info_record = tmp_path / "docker-info"
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
                "tag_proof_sha256": (
                    f"sha256:{'e' * 64}" if authority_mode == "published" else None
                ),
                "candidate_workflow": (
                    {
                        "run_id": "7",
                        "run_attempt": "1",
                        "head_sha": revision,
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
  info)
    test "${{2:-}}" = "--format"
    printf '%s\n' info >> "$TEST_DOCKER_INFO_RECORD"
    daemon_id=test-daemon-id
    if [[ "${{TEST_DAEMON_DRIFT_AFTER_PULL:-0}}" == 1 ]] && \
       [[ "$(wc -l < "$TEST_DOCKER_INFO_RECORD")" -gt 1 ]]; then
      daemon_id=changed-daemon-id
    fi
    printf '%s|%s\n' '{docker_root}' "$daemon_id"
    ;;
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
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DOCKER_CONFIG": str(docker_config),
        "DOCKER_HOST": "unix:///run/docker.sock",
        "OMEGA_GHCR_AUTH_ACTIVE": "1",
        "OMEGA_GHCR_PRIVATE_PACKAGES_VERIFIED": "1",
        "OMEGA_GCP_IMAGE_AUTHORITY_MODE": authority_mode,
        "TEST_DOCKER_INFO_RECORD": str(docker_info_record),
        "TEST_PULL_RECORD": str(pull_record),
    }

    result = subprocess.run(
        _preflight_command(
            preflight,
            "emmanuelnavaromero02-commits",
            image_tag,
            revision,
            version,
            str(lock_file),
        ),
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "PASS\t15/15" in result.stdout
    pulls = pull_record.read_text(encoding="utf-8").splitlines()
    assert len(pulls) == 15
    assert docker_info_record.read_text(encoding="utf-8").splitlines() == [
        "info",
        "info",
    ]
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
    published_authority = Path(f"{lock_file}.authority.json")
    assert published_authority.read_bytes() == authority_file.read_bytes()
    commit_file = Path(f"{lock_file}.commit.json")
    commit = json.loads(commit_file.read_bytes())
    assert commit == {
        "authority_mode": authority_mode,
        "authority_sha256": "sha256:"
        + hashlib.sha256(published_authority.read_bytes()).hexdigest(),
        "authority_size": published_authority.stat().st_size,
        "image_tag": image_tag,
        "lock_sha256": "sha256:" + hashlib.sha256(lock_file.read_bytes()).hexdigest(),
        "lock_size": lock_file.stat().st_size,
        "schema_version": 1,
        "source_sha": revision,
        "version": version,
    }
    assert commit_file.stat().st_mode & 0o777 == 0o400
    assert not list(lock_file.parent.glob("*.pending"))

    if authority_mode == "candidate":
        # An untrappable crash after publishing authority but before the lock
        # is recoverable only when the new run regenerates byte-identical data.
        recovery_dir = lock_file.parent.parent / f".{revision}.tmp.124"
        recovery_dir.mkdir(mode=0o700)
        recovery_lock = recovery_dir / "release-images.env"
        recovery_authority = Path(f"{recovery_lock}.authority.json")
        shutil.copyfile(authority_file, recovery_authority)
        recovery_authority.chmod(0o600)
        pull_record.unlink()
        docker_info_record.unlink()
        recovered = subprocess.run(
            _preflight_command(
                preflight,
                "emmanuelnavaromero02-commits",
                image_tag,
                revision,
                version,
                str(recovery_lock),
            ),
            cwd=ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert recovered.returncode == 0, recovered.stderr
        assert Path(f"{recovery_lock}.commit.json").is_file()
        assert not list(recovery_dir.glob("*.pending"))

        # The daemon identity is sampled again after all 15 pulls. A daemon
        # restart or storage-root swap cannot publish a mixed local cache.
        drift_dir = lock_file.parent.parent / f".{revision}.tmp.125"
        drift_dir.mkdir(mode=0o700)
        drift_lock = drift_dir / "release-images.env"
        pull_record.unlink()
        docker_info_record.unlink()
        drifted = subprocess.run(
            _preflight_command(
                preflight,
                "emmanuelnavaromero02-commits",
                image_tag,
                revision,
                version,
                str(drift_lock),
            ),
            cwd=ROOT,
            env={**environment, "TEST_DAEMON_DRIFT_AFTER_PULL": "1"},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert drifted.returncode == 9
        assert "daemon identity or storage root changed" in drifted.stderr
        assert len(pull_record.read_text(encoding="utf-8").splitlines()) == 15
        assert not Path(f"{drift_lock}.commit.json").exists()


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
        f"""#!/bin/sh
if [ "${{1:-}}" = info ]; then
  printf '%s\n' {shlex.quote(str(docker_root) + '|test-daemon-id')}
  exit 0
fi
touch {shlex.quote(str(docker_marker))}
exit 99
""",
    )

    preflight = _temporary_preflight(tmp_path, docker_root=docker_root)
    result = subprocess.run(
        _preflight_command(
            preflight,
            "emmanuelnavaromero02-commits",
            "latest",
            "b" * 40,
            "1.45.207-beta",
            str(tmp_path / "lock.env"),
        ),
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DOCKER_CONFIG": str(docker_config),
            "DOCKER_HOST": "unix:///run/docker.sock",
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
        f"""#!/bin/sh
if [ "${{1:-}}" = info ]; then
      printf '%s\n' {shlex.quote(str(docker_root) + '|test-daemon-id')}
  exit 0
fi
touch {shlex.quote(str(docker_marker))}
exit 99
""",
    )
    _write_executable(
        fake_bin / "df",
        "#!/bin/sh\nprintf 'Avail\\n1073741824\\n'\n",
    )
    revision = "b" * 40
    lock_file = _new_test_lock_file(tmp_path, revision)
    preflight = _temporary_preflight(tmp_path, docker_root=docker_root)
    result = subprocess.run(
        _preflight_command(
            preflight,
            "emmanuelnavaromero02-commits",
            f"candidate-{revision}",
            revision,
            "1.45.207-beta",
            str(lock_file),
        ),
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DOCKER_CONFIG": str(docker_config),
            "DOCKER_HOST": "unix:///run/docker.sock",
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


def test_preflight_rejects_docker_root_dir_mismatch_before_pull(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_calls = tmp_path / "docker-calls"
    df_marker = tmp_path / "df-ran"
    docker_config = tmp_path / "docker-config"
    docker_config.mkdir()
    (docker_config / "config.json").write_text("{}\n", encoding="utf-8")
    _write_test_auth_context(docker_config)
    docker_root = tmp_path / "docker-root"
    docker_root.mkdir()
    _write_executable(
        fake_bin / "docker",
        f"""#!/bin/sh
printf '%s\n' "$*" >> {shlex.quote(str(docker_calls))}
if [ "${{1:-}}" = info ]; then
  printf '%s\n' '/tmp/attacker-docker-root|attacker-id'
  exit 0
fi
exit 99
""",
    )
    _write_executable(
        fake_bin / "df",
        f"#!/bin/sh\ntouch {shlex.quote(str(df_marker))}\nexit 99\n",
    )
    revision = "b" * 40
    lock_file = _new_test_lock_file(tmp_path, revision)
    preflight = _temporary_preflight(tmp_path, docker_root=docker_root)

    result = subprocess.run(
        _preflight_command(
            preflight,
            "emmanuelnavaromero02-commits",
            f"candidate-{revision}",
            revision,
            "1.45.207-beta",
            str(lock_file),
        ),
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DOCKER_CONFIG": str(docker_config),
            "DOCKER_HOST": "unix:///run/docker.sock",
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
    assert "storage root differs" in result.stderr
    assert docker_calls.read_text(encoding="utf-8").splitlines() == [
        "info --format {{.DockerRootDir}}|{{.ID}}"
    ]
    assert not df_marker.exists()
    assert not lock_file.exists()
