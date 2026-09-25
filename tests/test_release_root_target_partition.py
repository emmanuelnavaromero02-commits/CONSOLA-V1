from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path

import pytest
import yaml


REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github/workflows/release.yml"
RUNNER = REPO / "scripts/run_release_pytest.py"


def _root_test_step() -> dict[str, object]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["validate-release"]["steps"]
    return next(step for step in steps if step.get("name") == "Run detected root tests")


def _fake_release_commands(tmp_path: Path) -> tuple[Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "invocations"

    sha256sum = fake_bin / "sha256sum"
    sha256sum.write_text(
        "#!/usr/bin/env bash\nprintf 'reviewed-verifier  %s\\n' \"$1\"\n",
        encoding="utf-8",
    )
    sha256sum.chmod(0o755)

    python3 = fake_bin / "python3"
    python3.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s|' \"${OMEGA_TEST_GRANTS_DSN-<unset>}\" >> \"${INVOCATION_LOG}\"\n"
        "printf '%s ' \"$@\" >> \"${INVOCATION_LOG}\"\n"
        "printf '\\n' >> \"${INVOCATION_LOG}\"\n",
        encoding="utf-8",
    )
    python3.chmod(0o755)
    return fake_bin, log


def _run_step(
    tmp_path: Path,
    targets: list[str],
    *,
    app_grants_dsn: str = "postgresql://postgres:test@127.0.0.1:55432/app_grants",
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    step = _root_test_step()
    fake_bin, log = _fake_release_commands(tmp_path)
    result = subprocess.run(
        ["bash", "-c", str(step["run"])],
        cwd=REPO,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "APP_GRANTS_DSN": app_grants_dsn,
            "INVOCATION_LOG": str(log),
            "OMEGA_RELEASE_TEST_HARNESS_SHA256": "reviewed-harness",
            "OMEGA_RELEASE_TEST_HARNESS_VERIFIER_SHA256": "reviewed-verifier",
            "ROOT_TEST_TARGETS": " ".join(targets),
        },
        text=True,
        capture_output=True,
        check=False,
    )
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return result, lines


def test_release_root_target_partition_is_explicit_and_exhaustive() -> None:
    source = str(_root_test_step()["run"])

    for family in (
        "root",
        "console",
        "refinement",
        "vault",
        "workspace",
        "mcp-infra",
    ):
        assert f"run_compatible_group {family}" in source
    assert 'scripts/run_release_pytest.py -q "${group[@]}"' in source
    assert 'path="${target%%::*}"' in source
    assert 'executed_targets=$((executed_targets + ${#group[@]}))' in source
    assert '"${executed_targets}" -eq "${#targets[@]}"' in source
    assert "has no reviewed import family" in source


def test_each_workflow_group_satisfies_release_pytest_import_root_contract() -> None:

    tree = ast.parse(RUNNER.read_text(encoding="utf-8"), filename=str(RUNNER))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_import_roots"
    )

    class ReleasePytestError(RuntimeError):
        pass

    namespace: dict[str, object] = {
        "Path": Path,
        "REPO": REPO,
        "ReleasePytestError": ReleasePytestError,
    }
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    exec(compile(module, str(RUNNER), "exec"), namespace)
    import_roots = namespace["_import_roots"]
    assert callable(import_roots)

    cases = {
        "root": (["tests/test_ci_changed_areas.py"], [REPO]),
        "console": (
            ["console/tests/test_agent_runner_scheduler_auth.py"],
            [REPO, REPO / "console"],
        ),
        "refinement": (
            ["refinement/tests/test_duckdb_s3_materialize_paths.py"],
            [REPO, REPO / "refinement"],
        ),
        "vault": (["vault/tests/test_encryption.py"], [REPO, REPO / "vault"]),
        "workspace": (
            ["workspace/tests/test_csrf.py"],
            [REPO, REPO / "workspace"],
        ),
        "mcp-infra": (
            ["mcp-infra/tests/test_contract.py"],
            [REPO, REPO / "mcp-infra"],
        ),
    }
    for targets, expected in cases.values():
        assert import_roots(targets) == expected

    with pytest.raises(ReleasePytestError, match="incompatible component"):
        import_roots(
            [
                "console/tests/test_agent_runner_scheduler_auth.py",
                "refinement/tests/test_duckdb_s3_materialize_paths.py",
            ]
        )


def test_release_root_targets_run_once_per_import_family_and_scope_app_grant_dsn(
    tmp_path: Path,
) -> None:
    targets = [
        "tests/test_analytic_app_dataset_grants.py",
        "console/tests/test_agent_runner_scheduler_auth.py",
        "refinement/tests/test_duckdb_s3_materialize_paths.py",
    ]
    result, lines = _run_step(tmp_path, targets)

    assert result.returncode == 0, result.stderr
    runner_lines = [line for line in lines if "scripts/run_release_pytest.py" in line]
    assert len(runner_lines) == 3
    assert runner_lines[0].startswith("postgresql://postgres:test@")
    assert runner_lines[0].endswith(f"{targets[0]} ")
    assert runner_lines[1] == (
        "<unset>|-I scripts/run_release_pytest.py -q " + targets[1] + " "
    )
    assert runner_lines[2] == (
        "<unset>|-I scripts/run_release_pytest.py -q " + targets[2] + " "
    )


def test_release_root_target_partition_rejects_an_unknown_family(
    tmp_path: Path,
) -> None:
    result, lines = _run_step(
        tmp_path,
        ["cartridges/sap_successfactors/tests/test_airflow_dag_contract.py"],
    )

    assert result.returncode != 0
    assert "has no reviewed import family" in result.stderr
    assert not any("scripts/run_release_pytest.py" in line for line in lines)


def test_app_grant_group_fails_closed_without_postgres_dsn(tmp_path: Path) -> None:
    result, lines = _run_step(
        tmp_path,
        ["tests/test_analytic_app_dataset_grants.py"],
        app_grants_dsn="",
    )

    assert result.returncode != 0
    assert "selected without their PostgreSQL DSN" in result.stderr
    assert not any("scripts/run_release_pytest.py" in line for line in lines)
