#!/usr/bin/env python3
from __future__ import annotations

import importlib
import sys
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root / "console"))
    try:
        signing = importlib.import_module(
            "app.services.control_room.business_evidence_signing"
        )
        signing.evidence_signing_keyring()
    except Exception:
        print("evidence signing keyring is invalid", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
