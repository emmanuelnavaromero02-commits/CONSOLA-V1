from __future__ import annotations

import os
import shlex
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
HELPER = REPO / "infra/terraform/deploy/ensure_evidence_env.sh"

ID_NAME = "CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID"
KEY_NAME = "CONTROL_ROOM_EVIDENCE_SIGNING_KEY"
PREVIOUS_NAME = "CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS"
FIXED_ID = "modecissions/control_room_evidence_signing_key_id"
FIXED_KEY = "modecissions/control_room_evidence_signing_key"
FIXED_PREVIOUS = "modecissions/control_room_evidence_signing_previous_keys"
CURRENT_ID = "evidence-current-v3"
CURRENT_KEY = "current-secret-value-" + ("k" * 48)
PREVIOUS_KEYS = '{"evidence-v2":"' + ("p" * 48) + '"}'
STDERR_SECRET = "aws-stderr-secret-must-not-leak"


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _fake_tools(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
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
    _write_executable(
        bin_dir / "stat",
        """#!/usr/bin/env bash
set -eu
printf '%s:%s\n' "$FAKE_STAT_UID" "$FAKE_STAT_MODE"
""",
    )
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
printf '%s\n' "$FAKE_AWS_STDERR_SECRET" >&2
if [[ "$secret_id" == "$FAKE_AWS_FAIL_REF" ]]; then
  exit 41
fi
if [[ "$secret_id" == "$FAKE_ID_REF" ]]; then
  printf '%s' "$FAKE_CURRENT_ID"
elif [[ "$secret_id" == "$FAKE_KEY_REF" ]]; then
  printf '%s' "$FAKE_CURRENT_KEY"
elif [[ "$secret_id" == "$FAKE_PREVIOUS_REF" ]]; then
  printf '%s' "$FAKE_PREVIOUS_KEYS"
else
  exit 42
fi
""",
    )
    _write_executable(bin_dir / "sleep", "#!/usr/bin/env bash\nexit 0\n")
    return bin_dir


def _dotenv_line(name: str, value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
    )
    return f'{name}="{escaped}"\n'


def _keyring_text(previous: str = "{}") -> str:
    return "".join(
        (
            _dotenv_line(ID_NAME, CURRENT_ID),
            _dotenv_line(KEY_NAME, CURRENT_KEY),
            _dotenv_line(PREVIOUS_NAME, previous),
        )
    )


def _run_helper(
    tmp_path: Path,
    shared: Path,
    *,
    config: str | None = None,
    id_ref: str = FIXED_ID,
    key_ref: str = FIXED_KEY,
    previous_ref: str = "__missing_optional_previous__",
    fail_ref: str = "__no_failure__",
    uid: str = "0",
    mode: str = "600",
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for name in list(env):
        if name.startswith("MODECISSIONS_SECRET_CONTROL_ROOM_EVIDENCE_"):
            env.pop(name)
    env.pop("AWS_ENV_FILE", None)
    env.pop("MODECISSIONS_CONTROL_ROOM_EVIDENCE_ENV_FILE", None)

    config_path = tmp_path / "aws-entrypoint.env"
    if config is not None:
        config_path.write_text(config, encoding="utf-8")
    env.update(
        {
            "PATH": f"{_fake_tools(tmp_path)}:{env['PATH']}",
            "MODECISSIONS_AWS_ENTRYPOINT_CONFIG": str(config_path),
            "FAKE_INSTALL_CALLS": str(tmp_path / "install.calls"),
            "FAKE_AWS_CALLS": str(tmp_path / "aws.calls"),
            "FAKE_STAT_UID": uid,
            "FAKE_STAT_MODE": mode,
            "FAKE_ID_REF": id_ref,
            "FAKE_KEY_REF": key_ref,
            "FAKE_PREVIOUS_REF": previous_ref,
            "FAKE_AWS_FAIL_REF": fail_ref,
            "FAKE_CURRENT_ID": CURRENT_ID,
            "FAKE_CURRENT_KEY": CURRENT_KEY,
            "FAKE_PREVIOUS_KEYS": PREVIOUS_KEYS,
            "FAKE_AWS_STDERR_SECRET": STDERR_SECRET,
        }
    )
    return subprocess.run(
        ["bash", str(HELPER), str(shared)],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize("existing", [None, "", f'{ID_NAME}="incomplete"\n'])
def test_missing_or_incomplete_private_env_materializes_operational_keyring(
    tmp_path: Path,
    existing: str | None,
) -> None:
    shared = tmp_path / ".env"
    private = tmp_path / ".env.control-room-evidence"
    original = 'APP_ENV="production"\n'
    shared.write_text(original, encoding="utf-8")
    if existing is not None:
        private.write_text(existing, encoding="utf-8")
        private.chmod(0o600)
    result = _run_helper(tmp_path, shared)
    assert result.returncode == 0, result.stdout + result.stderr
    assert private.read_text(encoding="utf-8") == _keyring_text()
    assert stat.S_IMODE(private.stat().st_mode) == 0o600
    assert shared.read_text(encoding="utf-8") == original
    assert (tmp_path / "aws.calls").read_text().splitlines() == [
        FIXED_ID,
        FIXED_KEY,
        FIXED_PREVIOUS,
    ]
    installs = (tmp_path / "install.calls").read_text(encoding="utf-8")
    assert "-m 600 -o root -g root" in installs
    assert not list(tmp_path.glob(".evidence-env.*"))
    _assert_no_secret_output(result, FIXED_ID, FIXED_KEY, FIXED_PREVIOUS)


def test_custom_path_and_configured_arns_materialize_private_keyring(
    tmp_path: Path,
) -> None:
    shared = tmp_path / ".env"
    original = 'MODECISSIONS_CONTROL_ROOM_EVIDENCE_ENV_FILE="private/evidence.env"\n'
    shared.write_text(original, encoding="utf-8")
    id_arn = "arn:test:evidence-id"
    key_arn = "arn:test:evidence-key"
    previous_arn = "arn:test:evidence-previous"
    config = (
        f"MODECISSIONS_SECRET_{ID_NAME}_ARN={id_arn}\n"
        f"MODECISSIONS_SECRET_{KEY_NAME}_ARN={key_arn}\n"
        f"MODECISSIONS_SECRET_{PREVIOUS_NAME}_ARN={previous_arn}\n"
    )

    result = _run_helper(
        tmp_path,
        shared,
        config=config,
        id_ref=id_arn,
        key_ref=key_arn,
        previous_ref=previous_arn,
    )

    private = tmp_path / "private/evidence.env"
    assert result.returncode == 0, result.stdout + result.stderr
    assert private.read_text(encoding="utf-8") == _keyring_text(PREVIOUS_KEYS)
    assert stat.S_IMODE(private.stat().st_mode) == 0o600
    assert shared.read_text(encoding="utf-8") == original
    assert (tmp_path / "aws.calls").read_text().splitlines() == [
        id_arn,
        key_arn,
        previous_arn,
    ]
    _assert_no_secret_output(result, id_arn, key_arn, previous_arn)


def test_complete_private_keyring_is_preserved_without_aws(tmp_path: Path) -> None:
    shared = tmp_path / ".env"
    private = tmp_path / ".env.control-room-evidence"
    original = _keyring_text(PREVIOUS_KEYS)
    shared.write_text('APP_ENV="production"\n', encoding="utf-8")
    private.write_text(original, encoding="utf-8")
    private.chmod(0o600)

    result = _run_helper(tmp_path, shared)

    assert result.returncode == 0, result.stdout + result.stderr
    assert private.read_text(encoding="utf-8") == original
    assert not (tmp_path / "aws.calls").exists()
    assert not (tmp_path / "install.calls").exists()


def test_required_fetch_failure_does_not_install_or_leak(tmp_path: Path) -> None:
    shared = tmp_path / ".env"
    original = 'APP_ENV="production"\n'
    shared.write_text(original, encoding="utf-8")

    result = _run_helper(tmp_path, shared, fail_ref=FIXED_KEY)

    assert result.returncode != 0
    assert not (tmp_path / ".env.control-room-evidence").exists()
    assert not (tmp_path / "install.calls").exists()
    assert shared.read_text(encoding="utf-8") == original
    _assert_no_secret_output(result, FIXED_ID, FIXED_KEY)


@pytest.mark.parametrize(("uid", "mode"), [("501", "600"), ("0", "644")])
def test_insecure_existing_private_keyring_is_rejected(
    tmp_path: Path,
    uid: str,
    mode: str,
) -> None:
    shared = tmp_path / ".env"
    private = tmp_path / ".env.control-room-evidence"
    shared.write_text('APP_ENV="production"\n', encoding="utf-8")
    private.write_text(_keyring_text(), encoding="utf-8")

    result = _run_helper(tmp_path, shared, uid=uid, mode=mode)

    assert result.returncode != 0
    assert "owned by root with mode 600" in result.stderr
    assert not (tmp_path / "aws.calls").exists()
    assert not (tmp_path / "install.calls").exists()


def test_private_env_symlink_is_rejected(tmp_path: Path) -> None:
    shared = tmp_path / ".env"
    private = tmp_path / ".env.control-room-evidence"
    target = tmp_path / "target"
    shared.write_text('APP_ENV="production"\n', encoding="utf-8")
    target.write_text(_keyring_text(), encoding="utf-8")
    private.symlink_to(target)

    result = _run_helper(tmp_path, shared)

    assert result.returncode != 0
    assert "must not be a symlink" in result.stderr
    assert not (tmp_path / "aws.calls").exists()
    assert not (tmp_path / "install.calls").exists()


def _assert_no_secret_output(result, *references: str) -> None:
    combined = result.stdout + result.stderr
    secrets = [CURRENT_ID, CURRENT_KEY, PREVIOUS_KEYS, STDERR_SECRET, *references]
    for sensitive in secrets:
        assert sensitive not in combined
