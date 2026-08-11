#!/usr/bin/env python3
"""Generate the immutable migration manifests used by the GCP day-2 runner.

The baseline checksums are expectations inferred from the exact source tree
currently running in GCP.  They are deliberately *not* represented as receipts
for historical executions.  The release lock is read from the current
worktree; the deployment controller separately binds that tree to the exact
candidate commit supplied as ``OMEGA_MIGRATION_CANDIDATE_REF``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASELINE_REF = "6b12883c5b5ea0537120279ccbee4947137998a2"
RELEASE_VERSION = "1.45.207-beta"
MANIFEST_DIR = ROOT / "infra" / "migrations" / "manifests"
BASELINE_PATH = MANIFEST_DIR / f"gcp-live-{BASELINE_REF}.json"
RELEASE_PATH = MANIFEST_DIR / f"v{RELEASE_VERSION}.json"
PENDING_OPERATIONAL = (
    "99zzt_analytic_app_dataset_grants.sql",
    "99zzu_analytic_app_manifest_registry.sql",
)
BASELINE_PROVENANCE = {
    "basis": "sha256_of_migration_files_in_git_tree_at_source_ref",
    "caveat": (
        "Expected bytes inferred from the pinned source tree; not "
        "contemporaneous proof of historical execution."
    ),
    "classification": "baseline_expected",
    "historical_execution_receipt": False,
    "source_ref": BASELINE_REF,
}

_OPERATIONAL = re.compile(r"^infra/init/[0-9]{2}.*_.*\.sql$")
_GOLD = re.compile(r"^infra/init_gold/[0-9]{2}_.*\.sql$")


def _git(*args: str, binary: bool = False) -> bytes | str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=not binary)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _entry(path: str, payload: bytes, *, gold: bool) -> dict[str, str]:
    name = Path(path).name
    return {
        "filename": f"gold/{name}" if gold else name,
        "path": path,
        "sha256": _sha256(payload),
    }


def _pinned_source_entries(ref: str, *, gold: bool) -> dict[str, str]:
    root = "infra/init_gold" if gold else "infra/init"
    matcher = _GOLD if gold else _OPERATIONAL
    names = str(_git("ls-tree", "-r", "--name-only", ref, "--", root)).splitlines()
    entries = [
        _entry(path, bytes(_git("show", f"{ref}:{path}", binary=True)), gold=gold)
        for path in sorted(names)
        if matcher.fullmatch(path)
    ]
    return {item["filename"]: item["sha256"] for item in entries}


def _worktree_entries(*, gold: bool) -> dict[str, str]:
    root = ROOT / ("infra/init_gold" if gold else "infra/init")
    pattern = "[0-9][0-9]_*.sql" if gold else "[0-9][0-9]*_*.sql"
    entries = [
        _entry(str(path.relative_to(ROOT)), path.read_bytes(), gold=gold)
        for path in sorted(root.glob(pattern))
    ]
    return {item["filename"]: item["sha256"] for item in entries}


def _baseline() -> dict[str, object]:
    return {
        "checksum_provenance": BASELINE_PROVENANCE,
        "schema_version": 1,
        "kind": "omega_database_migration_baseline",
        "environment": "gcp-canonical",
        "source_ref": BASELINE_REF,
        "databases": {
            "operational": _pinned_source_entries(BASELINE_REF, gold=False),
            "gold": _pinned_source_entries(BASELINE_REF, gold=True),
        },
    }


def _release() -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "omega_database_migration_release_lock",
        "release_version": RELEASE_VERSION,
        "candidate_ref_binding": "runtime_exact_40_hex_sha",
        "baseline_source_ref": BASELINE_REF,
        "evidence_contract": {
            "baseline": BASELINE_PROVENANCE,
            "pending": {
                "basis": (
                    "checksum_and_ledger_row_recorded_in_the_same_database_"
                    "transaction_as_the_migration"
                ),
                "classification": "guarded_transaction",
                "filenames": list(PENDING_OPERATIONAL),
            },
        },
        "allowed_new_migrations": {
            "operational": list(PENDING_OPERATIONAL),
            "gold": [],
        },
        "databases": {
            "operational": _worktree_entries(gold=False),
            "gold": _worktree_entries(gold=True),
        },
    }


def _serialized(value: dict[str, object]) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _write_immutable(path: Path, content: str) -> str:
    if path.exists():
        current = path.read_text(encoding="utf-8")
        if current != content:
            raise SystemExit(
                f"refusing to rewrite immutable manifest {path.name}; "
                "a future release requires a new filename"
            )
        return "unchanged"
    with path.open("x", encoding="utf-8") as destination:
        destination.write(content)
    return "created"


def _validate_relationship(
    baseline: dict[str, object], release: dict[str, object]
) -> None:
    if baseline["source_ref"] != release["baseline_source_ref"]:
        raise SystemExit("baseline source ref does not match the release lock")
    if release["allowed_new_migrations"]["operational"] != list(PENDING_OPERATIONAL):
        raise SystemExit("release lock does not declare the exact pending pair")
    if release["allowed_new_migrations"]["gold"]:
        raise SystemExit("the release must not introduce a Gold migration")

    for database in ("operational", "gold"):
        old = baseline["databases"][database]
        new = release["databases"][database]
        changed = sorted(name for name in old if new.get(name) != old[name])
        missing = sorted(set(old) - set(new))
        added = sorted(set(new) - set(old))
        allowed = sorted(release["allowed_new_migrations"][database])
        if changed or missing or added != allowed:
            raise SystemExit(
                f"{database} migration drift: changed={changed} "
                f"missing={missing} added={added} allowed={allowed}"
            )

    counts = {
        "baseline operational": len(baseline["databases"]["operational"]),
        "baseline Gold": len(baseline["databases"]["gold"]),
        "release operational": len(release["databases"]["operational"]),
        "release Gold": len(release["databases"]["gold"]),
    }
    expected = {
        "baseline operational": 198,
        "baseline Gold": 12,
        "release operational": 200,
        "release Gold": 12,
    }
    if counts != expected:
        raise SystemExit(f"unexpected migration counts: {counts}; expected {expected}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--write",
        action="store_true",
        help="create missing manifests; existing names are immutable",
    )
    args = parser.parse_args()

    baseline = _baseline()
    release = _release()
    _validate_relationship(baseline, release)
    expected = {
        BASELINE_PATH: _serialized(baseline),
        RELEASE_PATH: _serialized(release),
    }

    if args.write:
        MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
        for path, content in expected.items():
            action = _write_immutable(path, content)
            print(f"{action} {path.relative_to(ROOT)}")
        return 0

    for path, content in expected.items():
        if not path.is_file() or path.read_text(encoding="utf-8") != content:
            raise SystemExit(
                f"{path.relative_to(ROOT)} is stale; run "
                "scripts/generate_migration_manifests.py --write"
            )
    print("migration manifests are complete and byte-exact")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
