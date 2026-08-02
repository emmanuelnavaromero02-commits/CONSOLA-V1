from __future__ import annotations

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
    assert 'EXTENSIONS = ("httpfs", "postgres")' in installer


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
