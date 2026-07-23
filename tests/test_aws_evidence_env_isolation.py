from __future__ import annotations

import os
import re
import shlex
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
ENTRYPOINT = REPO / "scripts/aws-entrypoint.sh"
UPDATE_HELPER = REPO / "infra/terraform/deploy/ensure_evidence_env.sh"
DEPLOY = REPO / "infra/terraform/deploy"
AWS_COMPOSE = DEPLOY / "docker-compose.aws.yml"
CARTRIDGES_COMPOSE = DEPLOY / "docker-compose.cartridges.yml"
EVIDENCE_NAMES = (
    "CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID",
    "CONTROL_ROOM_EVIDENCE_SIGNING_KEY",
    "CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS",
)
CURRENT_KEY = "current-secret-value-" + ("k" * 32)
PREVIOUS_KEY = "previous-secret-value-" + ("p" * 32)
PREVIOUS_KEYS = f'{{"evidence-v1":"{PREVIOUS_KEY}"}}'


def _array(source: str, name: str) -> list[str]:
    match = re.search(rf"{name}=\((?P<body>[\s\S]*?)\n\)", source)
    assert match, f"missing shell array: {name}"
    return match.group("body").split()


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _fake_bin(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "aws",
        """#!/usr/bin/env bash
set -eu
secret_id=""
while (($#)); do
  if [[ "$1" == "--secret-id" ]]; then
    secret_id="$2"
    break
  fi
  shift
done
printf '%s\n' "$secret_id" >> "$FAKE_AWS_CALLS"
if [[ -n "${FAKE_AWS_STDERR_SECRET:-}" ]]; then
  printf '%s\n' "$FAKE_AWS_STDERR_SECRET" >&2
fi
if [[ "$secret_id" == "${FAKE_AWS_FAIL_ARN:-}" ]]; then
  exit 41
fi
case "$secret_id" in
  *:CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID) printf '%s' "$FAKE_CURRENT_ID" ;;
  *:CONTROL_ROOM_EVIDENCE_SIGNING_KEY) printf '%s' "$FAKE_CURRENT_KEY" ;;
  *:CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS) printf '%s' "$FAKE_PREVIOUS" ;;
  *) printf 'value-for-%s' "${secret_id##*:}" ;;
esac
""",
    )
    _write_executable(bin_dir / "sleep", "#!/usr/bin/env bash\nexit 0\n")

    real_install = shutil.which("install")
    assert real_install
    _write_executable(
        bin_dir / "install",
        f"""#!/usr/bin/env bash
set -eu
printf '%s\n' "$*" >> "$FAKE_INSTALL_CALLS"
args=()
while (($#)); do
  case "$1" in
    -o|-g) shift 2 ;;
    *) args+=("$1"); shift ;;
  esac
done
exec {shlex.quote(real_install)} "${{args[@]}}"
""",
    )
    return bin_dir


def _entrypoint_env(tmp_path: Path, *, custom_private: bool = False) -> dict[str, str]:
    source = ENTRYPOINT.read_text(encoding="utf-8")
    shared = tmp_path / "runtime.env"
    env = os.environ.copy()
    for name in list(env):
        if name.startswith("MODECISSIONS_") or name.startswith("FAKE_AWS_"):
            env.pop(name)
    env.update(
        {
            "PATH": f"{_fake_bin(tmp_path)}:{env['PATH']}",
            "APP_ENV": "development",
            "S3_BUCKET_NAME": "test-bucket",
            "CONSOLE_URL": "http://console.test",
            "WORKSPACE_PUBLIC_URL": "http://workspace.test",
            "MODECISSIONS_ENV_FILE": str(shared),
            "MODECISSIONS_AWS_ENTRYPOINT_CONFIG": str(tmp_path / "missing.env"),
            "MODECISSIONS_AWS_ENTRYPOINT_LOG": str(tmp_path / "entrypoint.log"),
            "FAKE_AWS_CALLS": str(tmp_path / "aws.calls"),
            "FAKE_INSTALL_CALLS": str(tmp_path / "install.calls"),
            "FAKE_CURRENT_ID": "evidence-current-v2",
            "FAKE_CURRENT_KEY": CURRENT_KEY,
            "FAKE_PREVIOUS": PREVIOUS_KEYS,
        }
    )
    if custom_private:
        env["MODECISSIONS_CONTROL_ROOM_EVIDENCE_ENV_FILE"] = str(
            tmp_path / "private" / "evidence.env"
        )
    required = _array(source, "required_secrets") + _array(
        source, "required_evidence_secrets"
    )
    for secret_name in required:
        env[f"MODECISSIONS_SECRET_{secret_name}_ARN"] = f"arn:test:{secret_name}"
    env["MODECISSIONS_SECRET_CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS_ARN"] = (
        "arn:test:CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS"
    )
    return env


def _run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(ENTRYPOINT)],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize("custom_private", [False, True])
def test_entrypoint_splits_evidence_env_with_secure_install_and_replay(
    tmp_path: Path, custom_private: bool
) -> None:
    env = _entrypoint_env(tmp_path, custom_private=custom_private)
    env.update(
        {
            "MODECISSIONS_ENV_TENANT_ID": "tenant-replay",
            "MODECISSIONS_ENV_WORKSPACE_ID": "workspace-replay",
            "MODECISSIONS_ENV_CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID": "poison-id",
            "MODECISSIONS_ENV_CONTROL_ROOM_EVIDENCE_SIGNING_KEY": "poison-key",
            "MODECISSIONS_ENV_CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS": "poison-old",
            "FAKE_AWS_STDERR_SECRET": "stderr-secret-must-not-leak",
        }
    )

    result = _run(env)

    assert result.returncode == 0, result.stdout + result.stderr
    shared = Path(env["MODECISSIONS_ENV_FILE"])
    private = Path(
        env.get(
            "MODECISSIONS_CONTROL_ROOM_EVIDENCE_ENV_FILE",
            f"{shared}.control-room-evidence",
        )
    )
    shared_text = shared.read_text(encoding="utf-8")
    private_text = private.read_text(encoding="utf-8")
    for name in EVIDENCE_NAMES:
        assert f"{name}=" not in shared_text
        assert private_text.count(f"{name}=") == 1
    assert 'TENANT_ID="tenant-replay"' in shared_text
    assert 'WORKSPACE_ID="workspace-replay"' in shared_text
    assert 'CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID="evidence-current-v2"' in private_text
    assert f'CONTROL_ROOM_EVIDENCE_SIGNING_KEY="{CURRENT_KEY}"' in private_text
    assert "poison" not in private_text
    assert f'MODECISSIONS_CONTROL_ROOM_EVIDENCE_ENV_FILE="{private}"' in shared_text
    assert not re.search(r"(?m)^FILE=", shared_text)
    assert stat.S_IMODE(shared.stat().st_mode) == 0o600
    assert stat.S_IMODE(private.stat().st_mode) == 0o600

    installs = (tmp_path / "install.calls").read_text(encoding="utf-8").splitlines()
    assert len(installs) == 2
    assert all("-m 600 -o root -g root" in call for call in installs)
    combined_log = (
        result.stdout + result.stderr + (tmp_path / "entrypoint.log").read_text()
    )
    for secret in (
        CURRENT_KEY,
        PREVIOUS_KEY,
        "stderr-secret-must-not-leak",
        "arn:test:",
    ):
        assert secret not in combined_log


def test_entrypoint_rejects_invalid_keyring_before_pair_publish(
    tmp_path: Path,
) -> None:
    env = _entrypoint_env(tmp_path)
    env["FAKE_PREVIOUS"] = "{not-json}"

    result = _run(env)

    combined_log = (
        result.stdout + result.stderr + (tmp_path / "entrypoint.log").read_text()
    )
    assert result.returncode == 1
    assert "evidence signing keyring validation failed" in combined_log
    assert "{not-json}" not in combined_log
    assert not Path(env["MODECISSIONS_ENV_FILE"]).exists()
    assert not Path(f"{env['MODECISSIONS_ENV_FILE']}.control-room-evidence").exists()


def test_update_rejects_evidence_key_equal_to_shared_security_key(
    tmp_path: Path,
) -> None:
    env = _entrypoint_env(tmp_path)
    shared = tmp_path / ".env"
    shared.write_text(
        f'SECURITY_CONTEXT_SIGNING_KEY="{CURRENT_KEY}"\n',
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(UPDATE_HELPER), str(shared)],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert CURRENT_KEY not in result.stdout + result.stderr
    assert not (tmp_path / ".env.control-room-evidence").exists()
    assert not (tmp_path / "install.calls").exists()


@pytest.mark.parametrize("failed_name", EVIDENCE_NAMES)
def test_evidence_fetch_failure_preserves_both_installed_files(
    tmp_path: Path, failed_name: str
) -> None:
    env = _entrypoint_env(tmp_path)
    shared = Path(env["MODECISSIONS_ENV_FILE"])
    private = Path(f"{shared}.control-room-evidence")
    shared.write_text("old-shared\n", encoding="utf-8")
    private.write_text("old-private\n", encoding="utf-8")
    env["FAKE_AWS_FAIL_ARN"] = f"arn:test:{failed_name}"
    env["FAKE_AWS_STDERR_SECRET"] = "failure-secret-must-not-leak"

    result = _run(env)

    assert result.returncode == 1
    assert shared.read_text(encoding="utf-8") == "old-shared\n"
    assert private.read_text(encoding="utf-8") == "old-private\n"
    assert not (tmp_path / "install.calls").exists()
    combined_log = (
        result.stdout + result.stderr + (tmp_path / "entrypoint.log").read_text()
    )
    assert "failure-secret-must-not-leak" not in combined_log
    assert "arn:test:" not in combined_log


@pytest.mark.parametrize("missing_name", EVIDENCE_NAMES[:2])
def test_current_evidence_arn_is_mandatory_without_partial_install(
    tmp_path: Path, missing_name: str
) -> None:
    env = _entrypoint_env(tmp_path)
    env.pop(f"MODECISSIONS_SECRET_{missing_name}_ARN")

    result = _run(env)

    assert result.returncode == 1
    assert "missing ARN env var" in result.stdout + result.stderr
    assert not Path(env["MODECISSIONS_ENV_FILE"]).exists()
    assert not Path(f"{env['MODECISSIONS_ENV_FILE']}.control-room-evidence").exists()
