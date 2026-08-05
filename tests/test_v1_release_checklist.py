from __future__ import annotations

import re
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


def test_v1_release_checklist_never_restates_a_version_literal():
    """The checklist must point at ``VERSION``, never copy it.

    It used to pin ``1.45.74-beta`` in prose, and this test froze that
    literal — so the doc drifted 131 patch releases behind the real
    ``VERSION`` while the suite stayed green. Assert coherence (no
    hardcoded release number at all) instead of a fixed string.
    """
    text = (REPO / "docs/release-checklist-v1.md").read_text(encoding="utf-8")
    stale = re.findall(r"\b\d+\.\d+\.\d+-beta\b", text)
    assert not stale, f"checklist restates version literals: {sorted(set(stale))}"
    assert "`VERSION` file at the repo root" in text


def test_v1_release_checklist_blocks_public_release_until_p2_green():
    text = (REPO / "docs/release-checklist-v1.md").read_text(encoding="utf-8")
    for needle in (
        "Current status: NOT APPROVED",
        "`make beta-smoke` green",
        "do not create a final `v1.0` or",
        "P2-19 live readiness | BLOCKED",
        "P2-20 live cartridges | BLOCKED",
        "P2-21 AWS/HTTPS staging | BLOCKED",
        "Full-stack release gate green",
        "v1.0 publica enterprise: no",
    ):
        assert needle in text
