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
UPDATE = REPO / "infra/terraform/deploy/update.sh"
WORKFLOW = REPO / ".github/workflows/deploy-aws.yml"


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
    return bin_dir


def _run_helper(
    tmp_path: Path,
    shared: Path,
    *,
    uid: str = "0",
    mode: str = "600",
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("AWS_ENV_FILE", None)
    env.pop("MODECISSIONS_CONTROL_ROOM_EVIDENCE_ENV_FILE", None)
    env.update(
        {
            "PATH": f"{_fake_tools(tmp_path)}:{env['PATH']}",
            "FAKE_INSTALL_CALLS": str(tmp_path / "install.calls"),
            "FAKE_STAT_UID": uid,
            "FAKE_STAT_MODE": mode,
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


def test_update_bootstrap_creates_missing_private_env_without_shared_keys(
    tmp_path: Path,
) -> None:
    shared = tmp_path / ".env"
    original = 'APP_ENV="production"\n'
    shared.write_text(original, encoding="utf-8")

    result = _run_helper(tmp_path, shared)

    private = tmp_path / ".env.control-room-evidence"
    assert result.returncode == 0, result.stdout + result.stderr
    assert private.read_bytes() == b""
    assert stat.S_IMODE(private.stat().st_mode) == 0o600
    assert shared.read_text(encoding="utf-8") == original
    installs = (tmp_path / "install.calls").read_text(encoding="utf-8")
    assert "-m 600 -o root -g root" in installs


def test_update_bootstrap_resolves_custom_private_path_from_dotenv(
    tmp_path: Path,
) -> None:
    shared = tmp_path / ".env"
    original = 'MODECISSIONS_CONTROL_ROOM_EVIDENCE_ENV_FILE="private/evidence.env"\n'
    shared.write_text(original, encoding="utf-8")

    result = _run_helper(tmp_path, shared)

    private = tmp_path / "private/evidence.env"
    assert result.returncode == 0, result.stdout + result.stderr
    assert private.read_bytes() == b""
    assert stat.S_IMODE(private.stat().st_mode) == 0o600
    assert shared.read_text(encoding="utf-8") == original


def test_update_bootstrap_preserves_existing_private_env(tmp_path: Path) -> None:
    shared = tmp_path / ".env"
    private = tmp_path / ".env.control-room-evidence"
    shared.write_text('APP_ENV="production"\n', encoding="utf-8")
    private.write_text("existing-evidence\n", encoding="utf-8")
    private.chmod(0o600)

    result = _run_helper(tmp_path, shared)

    assert result.returncode == 0, result.stdout + result.stderr
    assert private.read_text(encoding="utf-8") == "existing-evidence\n"
    assert not (tmp_path / "install.calls").exists()


@pytest.mark.parametrize(
    ("uid", "mode"),
    [("501", "600"), ("0", "644")],
)
def test_update_bootstrap_rejects_insecure_existing_private_env(
    tmp_path: Path,
    uid: str,
    mode: str,
) -> None:
    shared = tmp_path / ".env"
    private = tmp_path / ".env.control-room-evidence"
    shared.write_text('APP_ENV="production"\n', encoding="utf-8")
    private.write_text("existing-evidence\n", encoding="utf-8")

    result = _run_helper(tmp_path, shared, uid=uid, mode=mode)

    assert result.returncode != 0
    assert "owned by root with mode 600" in result.stderr
    assert private.read_text(encoding="utf-8") == "existing-evidence\n"


def test_update_bootstrap_rejects_private_env_symlink(tmp_path: Path) -> None:
    shared = tmp_path / ".env"
    private = tmp_path / ".env.control-room-evidence"
    target = tmp_path / "target"
    shared.write_text('APP_ENV="production"\n', encoding="utf-8")
    target.write_text("do-not-touch\n", encoding="utf-8")
    private.symlink_to(target)

    result = _run_helper(tmp_path, shared)

    assert result.returncode != 0
    assert "must not be a symlink" in result.stderr
    assert target.read_text(encoding="utf-8") == "do-not-touch\n"
    assert not (tmp_path / "install.calls").exists()


@pytest.mark.parametrize("launcher", [UPDATE, WORKFLOW])
def test_aws_update_launchers_ensure_private_env_before_deploy(
    launcher: Path,
) -> None:
    source = launcher.read_text(encoding="utf-8")

    assert "ensure_evidence_env.sh" in source
    assert source.index("bootstrap-keys.sh") < source.index("ensure_evidence_env.sh")
