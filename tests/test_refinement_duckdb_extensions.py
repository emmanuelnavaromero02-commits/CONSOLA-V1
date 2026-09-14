from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from refinement.app import duckdb_runtime


ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_duckdb_version_and_build_extensions_are_pinned() -> None:
    assert "duckdb==1.2.2" in _text("refinement/requirements.txt")
    dockerfile = _text("refinement/Dockerfile")
    assert "install_duckdb_extensions.py" in dockerfile
    assert dockerfile.index("pip install") < dockerfile.index(
        "install_duckdb_extensions.py"
    )
    installer = _text("refinement/scripts/install_duckdb_extensions.py")
    assert 'VERSION = "1.2.2"' in installer
    assert 'EXTENSIONS = ("httpfs", "postgres", "aws")' in installer


def test_refinement_declares_parquet_authority_runtime_dependency() -> None:
    requirements = _text("refinement/requirements.txt")
    verifier = _text("refinement/app/publication_objects.py")
    assert "pyarrow==23.0.1" in requirements
    assert "import pyarrow.parquet as pq" in verifier


def test_runtime_is_load_only_and_disables_extension_downloads() -> None:
    engine = _text("refinement/app/duckdb_engine.py")
    runtime = _text("refinement/app/duckdb_runtime.py")
    assert "INSTALL httpfs" not in engine and "INSTALL postgres" not in engine
    assert "INSTALL " not in runtime
    assert '"autoinstall_known_extensions": "false"' in runtime
    assert '"autoload_known_extensions": "false"' in runtime
    assert 'connection.execute(f"LOAD {extension};")' in runtime
    for path in (ROOT / "refinement/app").rglob("*.py"):
        assert "INSTALL httpfs" not in path.read_text(encoding="utf-8")
        assert "INSTALL postgres" not in path.read_text(encoding="utf-8")


def test_readiness_and_ci_enforce_offline_extensions() -> None:
    main = _text("refinement/app/main.py")
    workflow = _text(".github/workflows/control-room-postgres-rls.yml")
    preparation = _text("scripts/prepare_refinement_duckdb_ci.sh")
    runner = _text("scripts/run_refinement_duckdb_offline_smoke.sh")
    assert "require_loaded_extensions(engine._conn())" in main
    assert "prepare_refinement_duckdb_ci.sh" in workflow
    assert "run_refinement_duckdb_offline_smoke.sh" in preparation
    assert "--network none" in runner and "network create --internal" in runner
    assert 'test "$(id -u)" -ne 0' in runner
    assert "python -m scripts.duckdb_offline_smoke" in runner


def test_release_root_cache_is_fresh_frozen_and_network_independent() -> None:
    preparation = _text("scripts/prepare_refinement_duckdb_ci.sh")
    runner = _text("scripts/run_refinement_duckdb_offline_smoke.sh")
    exact_minio = (
        "quay.io/minio/minio:RELEASE.2024-12-18T13-15-44Z@"
        "sha256:1dce27c494a16bae114774f1cec295493f3613142713130c2d22dd5696be6ad3"
    )

    assert 'manifest="${DUCKDB_CACHE_MANIFEST:?' in preparation
    assert 'test ! -e "$duckdb_home"' in preparation
    assert 'test ! -e "$manifest"' in preparation
    assert 'test ! -L "$duckdb_home"' in preparation
    assert '> "$manifest"' in preparation
    assert "/tmp/refinement-duckdb-extensions.before" not in preparation
    assert "for extension in httpfs postgres_scanner aws" in preparation
    assert '"$extension.duckdb_extension"' in preparation
    assert 'f"{extension}.duckdb_extension.info"' in preparation
    assert "actual != expected" in preparation

    assert exact_minio in runner
    assert "docker pull" not in runner
    assert runner.count("--pull never") == runner.count("docker run")
    assert "--network none" in runner
    assert "network create --internal" in runner

    harness = _text("scripts/verify_release_test_harness.py")
    assert '"scripts/prepare_refinement_duckdb_ci.sh"' in harness
    assert '"scripts/run_refinement_duckdb_offline_smoke.sh"' in harness


def test_cache_preparation_rejects_regular_file_outside_exact_topology(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in
  build|run|rm|network) exit 0 ;;
  create) printf 'fake-container\n' ;;
  cp)
    destination="${@: -1}"
    target="${destination%/}/extensions/v1.2.2/linux_arm64"
    mkdir -p "${target}"
    for extension in httpfs postgres_scanner aws; do
      printf '%s' "${extension}" > "${target}/${extension}.duckdb_extension"
      printf '%s-info' "${extension}" > "${target}/${extension}.duckdb_extension.info"
    done
    if [[ "${FAKE_EXTRA_FILE:-0}" == 1 ]]; then
      printf 'extra' > "${target}/unexpected.duckdb_extension"
    fi
    ;;
  *) exit 97 ;;
esac
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    fake_uname = fake_bin / "uname"
    fake_uname.write_text("#!/usr/bin/env bash\nprintf 'Darwin\\n'\n", encoding="utf-8")
    fake_uname.chmod(0o755)

    def run(name: str, *, extra: bool) -> subprocess.CompletedProcess[str]:
        state = tmp_path / name
        state.mkdir()
        return subprocess.run(
            ["bash", "scripts/prepare_refinement_duckdb_ci.sh"],
            cwd=ROOT,
            env={
                **os.environ,
                "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                "DUCKDB_TEST_HOME": str(state / "home"),
                "DUCKDB_CACHE_MANIFEST": str(state / "extensions.sha256"),
                "REFINEMENT_IMAGE": "refinement:test-only",
                "PYTHON_BIN": sys.executable,
                "FAKE_EXTRA_FILE": "1" if extra else "0",
            },
            text=True,
            capture_output=True,
            check=False,
        )

    accepted = run("exact", extra=False)
    assert accepted.returncode == 0, accepted.stderr
    rejected = run("extra", extra=True)
    assert rejected.returncode != 0
    assert "file topology is invalid" in rejected.stderr


def test_missing_extension_fails_closed_with_sanitized_error(monkeypatch) -> None:
    class Connection:
        closed = False

        def execute(self, statement: str):
            if statement == "LOAD postgres;":
                raise RuntimeError("internal extension path")
            return self

        def close(self):
            self.closed = True

    connection = Connection()
    monkeypatch.setattr(duckdb_runtime.duckdb, "connect", lambda **_kwargs: connection)
    monkeypatch.setattr(duckdb_runtime.duckdb, "__version__", "1.2.2")
    with pytest.raises(RuntimeError) as caught:
        duckdb_runtime.connect_duckdb_runtime()
    assert str(caught.value) == "DuckDB required extensions are unavailable"
    assert connection.closed is True
