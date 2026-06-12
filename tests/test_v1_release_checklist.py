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


def test_v1_release_checklist_blocks_public_release_until_p2_green():
    text = (REPO / "docs/release-checklist-v1.md").read_text(encoding="utf-8")
    for needle in (
        "Current status: NOT APPROVED",
        "Current `VERSION`: `1.45.68-beta`",
        "`make beta-smoke` green",
        "do not create a final `v1.0` or",
        "P2-19 live readiness | BLOCKED",
        "P2-20 live cartridges | BLOCKED",
        "P2-21 AWS/HTTPS staging | BLOCKED",
        "Full-stack release gate green",
        "v1.0 publica enterprise: no",
    ):
        assert needle in text
