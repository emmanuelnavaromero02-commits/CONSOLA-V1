from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_workflow(workflow: str) -> dict[str, Any]:
    path = ROOT / ".github" / "workflows" / workflow
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_job(workflow: str, job_id: str) -> dict[str, Any]:
    return load_workflow(workflow)["jobs"][job_id]


def count_job_name(name: str) -> int:
    count = 0
    for path in (ROOT / ".github" / "workflows").glob("*.y*ml"):
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        count += sum(
            job.get("name", job_id) == name
            for job_id, job in (document.get("jobs") or {}).items()
        )
    return count


def assert_gate_shape(
    job: dict[str, Any],
    *,
    name: str,
    needs: list[str],
    env: dict[str, str],
) -> str:
    assert set(job) == {"name", "needs", "if", "runs-on", "timeout-minutes", "steps"}
    assert job["name"] == name
    assert job["needs"] == needs
    assert job["if"] == "${{ always() }}"
    assert job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] == 2
    assert len(job["steps"]) == 1

    step = job["steps"][0]
    assert set(step) == {"name", "shell", "env", "run"}
    assert step["shell"] == "bash"
    assert step["env"] == env
    script = step["run"]
    assert "set -euo pipefail" in script
    assert "checkout" not in script
    return script


def run_gate(
    script: str, env: dict[str, str], workdir: Path
) -> subprocess.CompletedProcess[str]:
    script_path = workdir / "gate.sh"
    script_path.write_text(script, encoding="utf-8")
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", script_path],
        cwd=workdir,
        env={"PATH": os.environ.get("PATH", ""), **env},
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )


def assert_gate_rejects(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode != 0
    assert "::error::" in result.stdout


def detector_outputs(*files: str) -> dict[str, str]:
    result = subprocess.run(
        ["python3", "scripts/ci_changed_areas.py", "--files", *files],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return dict(line.split("=", 1) for line in result.stdout.splitlines())
