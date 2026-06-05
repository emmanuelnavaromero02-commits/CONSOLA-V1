from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_b608_triage_doc_matches_security_workflow_policy():
    doc = (REPO / "docs" / "security" / "bandit-b608-triage.md").read_text(encoding="utf-8")
    workflow = (REPO / ".github" / "workflows" / "security.yml").read_text(encoding="utf-8")

    assert "165 `B608` findings" in doc
    assert "MEDIUM | HIGH | 0" in doc
    assert "bandit-b608-triage.md" in workflow
    assert "--confidence-level high" in workflow
