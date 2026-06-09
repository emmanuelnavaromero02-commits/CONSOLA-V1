#!/usr/bin/env python3
"""Guarded deploy rollback rehearsal gate."""

from __future__ import annotations

import json
import os
from pathlib import Path


def main() -> int:
    run_id = os.environ.get("OMEGA_ENTERPRISE_RUN_ID", "manual")
    root = Path(os.environ.get("OMEGA_ROLLBACK_EVIDENCE_DIR", f"docs/release-evidence/enterprise-readiness/{run_id}/rollback-rehearsal"))
    root.mkdir(parents=True, exist_ok=True)
    if os.environ.get("OMEGA_ROLLBACK_REHEARSAL_EXECUTE") != "1":
        status = "BLOCKED"
        note = "Rollback rehearsal requires explicit execute confirmation and a staging deployment target."
        unblock = "OMEGA_ROLLBACK_REHEARSAL_EXECUTE=1 PUBLIC_CONSOLE_URL=https://staging... make rollback-rehearsal"
    else:
        status = "BLOCKED"
        note = "Execution confirmed, but app/db N-1 rollback commands are not wired in this repository yet."
        unblock = "Wire deploy-canary/rollback scripts, then rerun rollback-rehearsal"
    payload = {"status": status, "note": note, "unblock": unblock}
    (root / "summary.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (root / "REPORT.md").write_text(
        "# Rollback Rehearsal\n\n"
        f"- status: `{status}`\n"
        f"- note: {note}\n"
        f"- unblock: `{unblock}`\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": status, "evidence_dir": str(root)}, indent=2))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
