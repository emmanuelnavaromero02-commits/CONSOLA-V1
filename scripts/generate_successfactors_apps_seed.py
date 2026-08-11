#!/usr/bin/env python3
"""Validate the immutable migration-87 seed and its runtime successor.

This command intentionally no longer rewrites migration 87.  The migration has
already executed in canonical GCP and its bytes are part of the database
history.  Current packaged HTML is reconciled by ``seed_packaged_apps`` during
Console startup.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "infra" / "init" / "87_sap_successfactors_apps_seed.sql"
APPS = ROOT / "cartridges" / "sap_successfactors" / "apps"
EXPECTED_SHA256 = "bbd5407ca36c32aa8efd6e4c8d94190d4fd80ea5c867e831a88f54a40887845d"
APP_NAMES = (
    "sap_successfactors_workforce_overview",
    "sap_successfactors_talent_health",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="retained for compatibility; validation is always read-only",
    )
    parser.parse_args()

    actual = hashlib.sha256(SEED.read_bytes()).hexdigest()
    if actual != EXPECTED_SHA256:
        raise SystemExit(
            "migration 87 is immutable database history and has changed: "
            f"expected {EXPECTED_SHA256}, got {actual}"
        )
    for name in APP_NAMES:
        html = APPS / f"{name}.html"
        metadata = APPS / f"{name}.json"
        if not html.is_file() or not html.read_text(encoding="utf-8").strip():
            raise SystemExit(f"runtime packaged HTML is missing: {name}")
        value = json.loads(metadata.read_text(encoding="utf-8"))
        if value.get("name") != name or not value.get("datasets_used"):
            raise SystemExit(f"runtime packaged metadata is incomplete: {name}")
    print("migration 87 is immutable; current packaged apps reconcile at runtime")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
