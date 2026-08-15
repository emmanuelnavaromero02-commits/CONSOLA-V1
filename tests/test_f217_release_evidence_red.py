"""Documentary and seal contracts carried into the v1.45.219 recovery."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
EVIDENCE = REPO / "docs/release-evidence/omega-f2-digest-release-gate.md"
SEAL = REPO / ".github/release-test-harness-seal.json"
EXPECTED_TESTS = {
    "tests/test_f217_migration_lock_red.py",
    "tests/test_f217_release_evidence_red.py",
    "tests/test_f217_transition_bridge_red.py",
}


def _evidence() -> str:
    return EVIDENCE.read_text(encoding="utf-8")


def _f216_run_evidence() -> str:
    marker = "Workflow run `31858396322`"
    document = _evidence()
    assert marker in document
    return document.split(marker, 1)[1].split("\n## ", 1)[0]


def test_f219_version_and_forward_recovery_target_are_exact() -> None:
    assert (REPO / "VERSION").read_text(encoding="utf-8").strip() == ("1.45.219-beta")
    document = _evidence()
    compact = " ".join(document.split())
    assert "forward recovery as `v1.45.219-beta`" in compact
    assert "v1.45.210-beta` through `v1.45.218-beta`" in compact
    assert (
        ".210 -> .211 -> .212 -> .213 -> .214 -> .215 -> .216 -> .217 -> .218 -> .219"
        in compact
    )


def test_f216_failed_run_identity_and_lock_cause_are_retained() -> None:
    evidence = _f216_run_evidence()
    required = (
        "`v1.45.216-beta`",
        "`0f47139b7c3e8ba2b907804d0b2a3673da3a000c`",
        "`1544f511cb49375120e75d04e6f8b18c7564f3e7`",
        "`82a7e45adff10b4877b1bfb0e6acab4206744c60`",
        "Run all final gates against exact digest stack",
        "apply_db_migrations.sh",
        "COMPOSE_FILE",
        "exit 97",
    )
    assert all(token in evidence for token in required)


def test_f216_manifest_receipts_and_artifact_hashes_are_retained() -> None:
    evidence = _f216_run_evidence()
    required = (
        "exactly 16 workflow artifacts",
        "omega-release-manifest-31858396322-1",
        "`9239950258`",
        "`00615a2030a6873ed37729782fe08f6495332ce410f3367b824a15ba33c33aca`",
        "`973748408592f6df5b55cab1b75dcef72c46ca1fcffff147788be75cd0fd0fb1`",
        "`5e3f9874f1bd3ed7d343c49b4a97536440282e86c1a1488e588eed07e9f5a79d`",
        "15/15",
        "receipts",
        "manifest",
    )
    assert all(token in evidence for token in required)
    assert re.search(r"15 (?:image-)?candidate receipts", evidence)


def test_f216_private_tagless_and_absent_release_evidence_are_retained() -> None:
    evidence = _f216_run_evidence()
    required = (
        "15/15 packages",
        "private",
        "tagless",
        "`tags: []`",
        "publish-release-manifest",
        "skipped",
        "GitHub Release",
        "including drafts",
        "absent",
    )
    assert all(token in evidence for token in required)


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
