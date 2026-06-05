from __future__ import annotations

import tomllib
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_root_metadata_declares_python_312_floor():
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))

    assert data["project"]["requires-python"] == ">=3.12"


def test_ci_and_docker_use_python_312():
    dockerfiles = [
        REPO / "console" / "Dockerfile",
        REPO / "workspace" / "Dockerfile",
        REPO / "vault" / "Dockerfile",
        REPO / "refinement" / "Dockerfile",
    ]
    for dockerfile in dockerfiles:
        assert "python:3.12" in dockerfile.read_text(encoding="utf-8")

    workflow_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (REPO / ".github" / "workflows").glob("*.yml")
    )
    assert "python-version: \"3.12\"" in workflow_text or "python-version: '3.12'" in workflow_text
