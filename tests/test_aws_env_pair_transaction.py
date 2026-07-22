from __future__ import annotations

import os
import shlex
import shutil
import stat
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / "scripts/aws-env-pair.sh"
ENTRYPOINT = REPO / "scripts/aws-entrypoint.sh"


def test_env_pair_shell_files_stay_bounded() -> None:
    assert len(MODULE.read_text(encoding="utf-8").splitlines()) <= 300
    assert len(ENTRYPOINT.read_text(encoding="utf-8").splitlines()) <= 300


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _fault_tools(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    real_install = shutil.which("install")
    real_mv = shutil.which("mv")
    assert real_install and real_mv

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
        bin_dir / "mv",
        f"""#!/usr/bin/env bash
set -eu
target="${{!#}}"
if [[ "$target" == "$FAKE_MV_FAIL_TARGET" && ! -e "$FAKE_MV_FAILURE_MARKER" ]]; then
  : > "$FAKE_MV_FAILURE_MARKER"
  exit 73
fi
exec {shlex.quote(real_mv)} "$@"
""",
    )
    return bin_dir


def test_second_publish_rename_failure_rolls_back_both_destinations(
    tmp_path: Path,
) -> None:
    shared = tmp_path / "shared.env"
    evidence = tmp_path / "evidence.env"
    shared.write_text("old-shared\n", encoding="utf-8")
    evidence.write_text("old-evidence\n", encoding="utf-8")
    shared.chmod(0o640)
    evidence.chmod(0o600)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{_fault_tools(tmp_path)}:{env['PATH']}",
            "FAKE_INSTALL_CALLS": str(tmp_path / "install.calls"),
            "FAKE_MV_FAIL_TARGET": str(shared),
            "FAKE_MV_FAILURE_MARKER": str(tmp_path / "mv.failed"),
        }
    )
    harness = f"""set -Eeuo pipefail
source {shlex.quote(str(MODULE))}
aws_env_pair_init {shlex.quote(str(shared))} {shlex.quote(str(evidence))}
write_env SHARED_SECRET new-shared-secret
write_evidence_env EVIDENCE_SECRET new-evidence-secret
publish_env_pair
"""

    result = subprocess.run(
        ["bash", "-c", harness],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert (tmp_path / "mv.failed").exists()
    assert shared.read_text(encoding="utf-8") == "old-shared\n"
    assert evidence.read_text(encoding="utf-8") == "old-evidence\n"
    assert stat.S_IMODE(shared.stat().st_mode) == 0o640
    assert stat.S_IMODE(evidence.stat().st_mode) == 0o600
    assert len((tmp_path / "install.calls").read_text().splitlines()) == 2
    assert not list(tmp_path.glob("*.tmp.*"))
    assert not list(tmp_path.glob("*.backup.*"))
    combined = result.stdout + result.stderr
    assert "restoring previous files" in combined
    assert "new-shared-secret" not in combined
    assert "new-evidence-secret" not in combined
