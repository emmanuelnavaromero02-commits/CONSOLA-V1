from __future__ import annotations

import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_version_stays_beta_while_p2_is_blocked():
    version = (REPO / "VERSION").read_text(encoding="utf-8").strip()
    assert version.endswith("-beta")
    assert version != "1.0.0"


def test_no_final_v1_tag_exists_while_p2_is_blocked():
    output = subprocess.check_output(
        ["git", "tag", "--list"],
        cwd=REPO,
        text=True,
    )
    tags = {line.strip() for line in output.splitlines() if line.strip()}
    assert "v1.0" not in tags
    assert "v1.0.0" not in tags


def test_baseline_smoke_names_required_workflows_explicitly():
    smoke = (REPO / "scripts/baseline_smoke.sh").read_text(encoding="utf-8")
    assert "REQUIRED_WORKFLOWS" in smoke
    for workflow in (
        ".github/workflows/lint.yml",
        ".github/workflows/security.yml",
        ".github/workflows/mcp-infra-pdf-security.yml",
        ".github/workflows/control-room-postgres-rls.yml",
    ):
        assert workflow in smoke, f"{workflow} is no longer pinned in the smoke"
        assert (REPO / workflow).is_file(), f"{workflow} is required but absent"


def test_baseline_bandit_scope_matches_security_workflow():
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    for helper in ("scripts/ci_changed_areas.py", "scripts/ci_control_room_paths.py"):
        assert helper in makefile, f"local bandit scope omits {helper}, CI audits it"
