from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SEAL = REPO / ".github/release-test-harness-seal.json"
EXPECTED_TESTS = {
    "tests/test_f217_migration_lock_red.py",
    "tests/test_f217_release_evidence_red.py",
    "tests/test_f217_transition_bridge_red.py",
}


def test_f217_red_pack_is_in_the_release_harness_inventory() -> None:
    payload = json.loads(SEAL.read_text(encoding="utf-8"))
    sealed_paths = {entry["path"] for entry in payload["files"]}
    assert EXPECTED_TESTS <= sealed_paths


def test_f217_release_harness_seal_matches_the_checkout() -> None:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "OMEGA_RELEASE_DIGEST_STACK",
            "OMEGA_RELEASE_TEST_HARNESS_SHA256",
            "OMEGA_RELEASE_TEST_SKIP_ENVIRONMENT",
        }
    }
    result = subprocess.run(
        [sys.executable, "-I", "scripts/verify_release_test_harness.py"],
        cwd=REPO,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "RELEASE TEST HARNESS PASS" in result.stdout
