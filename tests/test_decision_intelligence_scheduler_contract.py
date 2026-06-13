from __future__ import annotations

import ast
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts/run_intelligence_scheduled.py"
MAKEFILE = REPO / "Makefile"


def test_makefile_exposes_controlled_intelligence_scheduler_targets():
    makefile = MAKEFILE.read_text(encoding="utf-8")

    assert "run-intelligence-scheduled-local:" in makefile
    assert "scripts/run_intelligence_scheduled.py --target local" in makefile
    assert "run-intelligence-scheduled-aws:" in makefile
    assert "scripts/run_intelligence_scheduled.py --target aws" in makefile


def test_scheduler_script_is_ssm_backed_and_redacts_evidence():
    source = SCRIPT.read_text(encoding="utf-8")
    ast.parse(source)

    for needle in (
        "send_ssm_script",
        "resolve_instance_id",
        "redact",
        "stdout_redacted.txt",
        "stderr_redacted.txt",
        "ssm_command_id",
        "instance_id",
        "region",
        "OMEGA_INTELLIGENCE_SCHEDULED_JSON=",
        "OMEGA_INTELLIGENCE_WORKSPACE_ID is required",
    ):
        assert needle in source
    assert "printenv" not in source
    assert "infra/.env" not in source
    assert "cat .env" not in source
    assert "source .env" not in source


def test_scheduler_script_blocks_external_writeback_and_uses_scheduled_run_mode():
    source = SCRIPT.read_text(encoding="utf-8")

    assert "CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK" in source
    assert "REFUSE: external write-back enabled" in source
    assert '"run_mode": "scheduled"' in source
    assert '"include_external": False' in source
    assert '"dry_run": dry_run' in source
    assert "run_intelligence(user, body, persist=True)" in source
