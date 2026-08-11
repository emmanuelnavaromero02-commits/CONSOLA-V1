from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

import pytest

from scripts import generate_migration_manifests as manifest_generator
from scripts import migration_guard as guard


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_REF = "1" * 40


def _args() -> argparse.Namespace:
    manifests = ROOT / "infra" / "migrations" / "manifests"
    return argparse.Namespace(
        old_ref=guard.BASELINE_REF,
        candidate_ref=CANDIDATE_REF,
        release_version=guard.RELEASE_VERSION,
        baseline_manifest=str(manifests / guard.BASELINE_NAME),
        baseline_manifest_sha256=guard.PINNED_BASELINE_MANIFEST_SHA256,
        release_manifest=str(manifests / guard.RELEASE_NAME),
        release_manifest_sha256=guard.PINNED_RELEASE_MANIFEST_SHA256,
    )


def _rows(
    contract: guard.Contract,
    database: str,
    *,
    state: str,
    attested: bool = False,
) -> dict[str, guard.LedgerRow]:
    names = (
        contract.baseline[database]
        if state == "baseline"
        else contract.release[database]
    )
    result = {}
    for filename in names:
        source, manifest = guard._expected_provenance(contract, database, filename)
        result[filename] = guard.LedgerRow(
            checksum=contract.release[database][filename] if attested else None,
            source_ref=source if attested else None,
            manifest_sha256=manifest if attested else None,
            attested=attested,
        )
    return result


def test_complete_manifests_are_exact_and_only_add_the_pending_pair() -> None:
    contract = guard._validate_contract(_args())
    assert len(contract.baseline["operational"]) == 198
    assert len(contract.baseline["gold"]) == 12
    assert len(contract.release["operational"]) == 200
    assert len(contract.release["gold"]) == 12
    assert sorted(
        set(contract.release["operational"]) - set(contract.baseline["operational"])
    ) == sorted(guard.PENDING_OPERATIONAL)
    assert contract.release["gold"] == contract.baseline["gold"]


def test_manifest_generator_check_is_append_only_and_reproducible() -> None:
    result = subprocess.run(
        ["python3", "scripts/generate_migration_manifests.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "complete and byte-exact" in result.stdout


def test_manifest_writer_is_create_only_and_never_rewrites(tmp_path: Path) -> None:
    path = tmp_path / "v1.45.207-beta.json"
    assert manifest_generator._write_immutable(path, "locked\n") == "created"
    assert manifest_generator._write_immutable(path, "locked\n") == "unchanged"
    with pytest.raises(SystemExit, match="refusing to rewrite immutable manifest"):
        manifest_generator._write_immutable(path, "changed\n")
    assert path.read_text(encoding="utf-8") == "locked\n"


def test_partial_pending_pair_and_unknown_rows_fail_closed() -> None:
    contract = guard._validate_contract(_args())
    partial = _rows(contract, "operational", state="baseline")
    filename = guard.PENDING_OPERATIONAL[0]
    partial[filename] = guard.LedgerRow(None, None, None, False)
    with pytest.raises(SystemExit, match="partial/tampered"):
        guard._validate_ledger(contract, "operational", partial, require_release=False)

    unknown = _rows(contract, "gold", state="release")
    unknown["gold/99_unknown.sql"] = guard.LedgerRow(None, None, None, False)
    with pytest.raises(SystemExit, match="partial/tampered"):
        guard._validate_ledger(contract, "gold", unknown, require_release=False)


def test_release_filenames_with_null_pending_attestation_are_not_blessed() -> None:
    contract = guard._validate_contract(_args())
    fresh = _rows(contract, "operational", state="release")
    with pytest.raises(SystemExit, match="refusing to bless"):
        guard._validate_ledger(contract, "operational", fresh, require_release=False)
    assert (
        guard._validate_ledger(
            contract,
            "operational",
            fresh,
            require_release=False,
            allow_bootstrap_release=True,
        )
        == "bootstrap_release"
    )

    partial = dict(fresh)
    filename = guard.PENDING_OPERATIONAL[0]
    source, manifest = guard._expected_provenance(contract, "operational", filename)
    partial[filename] = guard.LedgerRow(
        contract.release["operational"][filename], source, manifest, True
    )
    with pytest.raises(SystemExit, match="refusing to bless"):
        guard._validate_ledger(
            contract,
            "operational",
            partial,
            require_release=False,
            allow_bootstrap_release=True,
        )


def test_tampered_checksum_or_provenance_fails_before_writes() -> None:
    contract = guard._validate_contract(_args())
    rows = _rows(contract, "operational", state="baseline", attested=True)
    filename = next(iter(rows))
    original = rows[filename]
    rows[filename] = guard.LedgerRow(
        checksum="0" * 64,
        source_ref=original.source_ref,
        manifest_sha256=original.manifest_sha256,
        attested=True,
    )
    with pytest.raises(SystemExit, match="checksum mismatch"):
        guard._validate_ledger(contract, "operational", rows, require_release=False)

    rows = _rows(contract, "gold", state="release", attested=True)
    filename = next(iter(rows))
    original = rows[filename]
    rows[filename] = guard.LedgerRow(
        checksum=original.checksum,
        source_ref="2" * 40,
        manifest_sha256=original.manifest_sha256,
        attested=True,
    )
    with pytest.raises(SystemExit, match="source provenance mismatch"):
        guard._validate_ledger(contract, "gold", rows, require_release=False)


def test_ledger_json_preserves_sql_null_and_rejects_sentinel_strings(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ledger.jsonl"
    path.write_text(
        json.dumps(
            {
                "filename": "00_schema.sql",
                "checksum": "-",
                "source_ref": None,
                "manifest_sha256": None,
                "attested": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="checksum is invalid"):
        guard._load_ledger(path, "operational")


def test_partial_metadata_is_repaired_but_cannot_pass_postflight() -> None:
    contract = guard._validate_contract(_args())
    rows = _rows(contract, "gold", state="release", attested=True)
    filename = next(iter(rows))
    original = rows[filename]
    rows[filename] = guard.LedgerRow(
        checksum=original.checksum,
        source_ref=original.source_ref,
        manifest_sha256=original.manifest_sha256,
        attested=False,
    )
    assert (
        guard._validate_ledger(contract, "gold", rows, require_release=False)
        == "release"
    )
    with pytest.raises(SystemExit, match="not fully attested"):
        guard._validate_ledger(contract, "gold", rows, require_release=True)
    sql = guard._render_sql(contract, "gold", "release")
    assert "OR sm.checksum_attested_at IS NULL" in sql


def test_retry_accepts_gold_attested_with_operational_still_at_baseline() -> None:
    contract = guard._validate_contract(_args())
    operational = _rows(contract, "operational", state="baseline")
    gold = _rows(contract, "gold", state="release", attested=True)
    assert (
        guard._validate_ledger(
            contract, "operational", operational, require_release=False
        )
        == "baseline"
    )
    assert (
        guard._validate_ledger(contract, "gold", gold, require_release=False)
        == "release"
    )


def test_pending_pair_is_one_psql_transaction_without_nested_terminators() -> None:
    contract = guard._validate_contract(_args())
    sql = guard._render_sql(contract, "operational", "baseline")
    assert sql.splitlines()[1] == "BEGIN;"
    assert sql.rstrip().endswith("COMMIT;")
    assert sql.count("\nBEGIN;") == 1
    assert sql.count("\nCOMMIT;") == 1
    first = sql.index(f"\\i /docker-entrypoint-initdb.d/{guard.PENDING_OPERATIONAL[0]}")
    second = sql.index(
        f"\\i /docker-entrypoint-initdb.d/{guard.PENDING_OPERATIONAL[1]}"
    )
    assert first < second < sql.rindex("COMMIT;")
    terminator = re.compile(
        r"^\s*(?:BEGIN|COMMIT|ROLLBACK|START\s+TRANSACTION)\s*;",
        flags=re.IGNORECASE | re.MULTILINE,
    )
    for filename in guard.PENDING_OPERATIONAL:
        source = (ROOT / "infra" / "init" / filename).read_text(encoding="utf-8")
        assert not terminator.search(source), filename


def test_shell_orders_two_database_preflight_then_gold_then_operational() -> None:
    script = (ROOT / "scripts" / "apply_db_migrations.sh").read_text(encoding="utf-8")
    preflight = script.index('python3 "$GUARD" preflight')
    gold = script.index('render-sql --plan "$PLAN" --database gold')
    operational = script.index('render-sql --plan "$PLAN" --database operational')
    assert script.index("dump_operational_ledger >") < preflight
    assert script.index("dump_gold_ledger >") < preflight
    assert preflight < gold < operational
    assert "OMEGA_MIGRATION_REQUIRE_EXPLICIT_CONTRACT" in script
    assert "bootstrap ledger attestation is forbidden" in script
    assert 'exec -T postgres sha256sum "$container_path"' in script
    for checksum in (
        "99f87377bf875474ae0be08ce69dac28a84a4951fab7f49655ac229cfd4e975f",
        "adad0c9f710ce80aa5ec46dad16b919233f1f0f20ad8fd6d9af7984197577123",
    ):
        assert checksum in script


def test_plan_contains_no_unreviewed_fields(tmp_path: Path) -> None:
    contract = guard._validate_contract(_args())
    plan = tmp_path / "plan.json"
    guard._write_plan(contract, "baseline", "release", plan)
    value = json.loads(plan.read_text(encoding="utf-8"))
    assert value["candidate_ref"] == CANDIDATE_REF
    assert value["operational_state"] == "baseline"
    assert value["gold_state"] == "release"


def test_control_room_ci_runs_and_fail_closes_the_real_migration_gate() -> None:
    workflow = (
        ROOT / ".github" / "workflows" / "control-room-postgres-rls.yml"
    ).read_text(encoding="utf-8")
    step = workflow.split("- name: Run immutable migration release gate", 1)[1]
    step = step.split("- name: Run live PostgreSQL/RLS tests", 1)[0]
    for path in (
        "tests/test_migration_release_guard.py",
        "tests/test_migration_release_guard_live.py",
        "tests/test_successfactors_apps_seed_sync.py",
        "tests/test_apply_db_migrations_script.py",
    ):
        assert path in step
    assert "--junitxml=/tmp/migration-release-guard.xml" in step
    assert (
        'verify_junit("/tmp/migration-release-guard.xml", minimum=21, '
        'label="Migration release guard")' in workflow
    )
