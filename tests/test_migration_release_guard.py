from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from types import SimpleNamespace
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import generate_migration_manifests as manifest_generator
from scripts import migration_guard as guard


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_REF = subprocess.check_output(
    ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
).strip()


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


def _success_receipt(tmp_path: Path) -> tuple[Path, str, str, str]:
    candidate = "a" * 40
    manifest_sha = "b" * 64
    run_id = "omega_migration_123_456"
    root = tmp_path / "operation-receipts"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    receipt = root / f"migration-{candidate}-20260812T010203Z-123"
    receipt.mkdir(mode=0o700)
    receipt.chmod(0o700)
    for number, (filename, (phase, status_value, commit_state)) in enumerate(
        guard.MIGRATION_SUCCESS_EVENTS.items()
    ):
        payload = {
            "candidate_ref": candidate,
            "database_commit_state": commit_state,
            "exit_code": 0,
            "operation": "gcp_day2_database_migration",
            "phase": phase,
            "recorded_at": f"2026-08-12T01:02:{number:02d}Z",
            "release_manifest_sha256": manifest_sha,
            "release_version": guard.RELEASE_VERSION,
            "run_id": run_id,
            "schema_version": 1,
            "status": status_value,
        }
        event = receipt / filename
        event.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        event.chmod(0o400)
    return receipt, candidate, manifest_sha, run_id


def _validate_success_receipt(
    receipt: Path, candidate: str, manifest_sha: str, run_id: str
) -> dict[str, object]:
    return guard._validate_migration_success_receipt(
        receipt,
        candidate_ref=candidate,
        release_version=guard.RELEASE_VERSION,
        release_manifest_sha256=manifest_sha,
        run_id=run_id,
        canonical_root=receipt.parent,
        expected_uid=os.geteuid(),
        expected_gid=os.getegid(),
    )


def test_exact_immutable_migration_success_receipt_validates(tmp_path: Path) -> None:
    receipt, candidate, manifest_sha, run_id = _success_receipt(tmp_path)
    validation = _validate_success_receipt(receipt, candidate, manifest_sha, run_id)
    assert validation == {
        "candidate_ref": candidate,
        "database_commit_state": "gold_operational_and_authority",
        "receipt_dir": str(receipt),
        "release_manifest_sha256": manifest_sha,
        "release_version": guard.RELEASE_VERSION,
        "run_id": run_id,
        "status": "PASS",
    }


def test_migration_success_receipt_rejects_inventory_payload_and_links(
    tmp_path: Path,
) -> None:
    receipt, candidate, manifest_sha, run_id = _success_receipt(tmp_path)
    extra = receipt / "attacker.json"
    extra.write_text("{}\n", encoding="utf-8")
    extra.chmod(0o400)
    with pytest.raises(SystemExit, match="inventory"):
        _validate_success_receipt(receipt, candidate, manifest_sha, run_id)
    extra.unlink()

    terminal = receipt / "90-terminal.json"
    terminal.chmod(0o600)
    payload = json.loads(terminal.read_text(encoding="utf-8"))
    payload["status"] = "IN_PROGRESS"
    terminal.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    terminal.chmod(0o400)
    with pytest.raises(SystemExit, match="contract differs"):
        _validate_success_receipt(receipt, candidate, manifest_sha, run_id)

    payload["status"] = "PASS"
    terminal.chmod(0o600)
    terminal.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    terminal.chmod(0o400)
    outside_link = tmp_path / "terminal-hardlink"
    os.link(terminal, outside_link)
    with pytest.raises(SystemExit, match="not immutable"):
        _validate_success_receipt(receipt, candidate, manifest_sha, run_id)


def _rows(
    contract: guard.Contract,
    database: str,
    *,
    state: str,
    profile: str = "blank",
) -> dict[str, guard.LedgerRow]:
    names = (
        contract.baseline[database]
        if state == "baseline"
        else contract.release[database]
    )
    result = {}
    for filename in names:
        source, manifest = guard._expected_provenance(contract, database, filename)
        is_pending = database == "operational" and filename in guard.PENDING_OPERATIONAL
        evidence_kind = (
            guard.GUARDED_EVIDENCE
            if profile == "guarded" and is_pending
            else guard.BASELINE_EVIDENCE
        )
        populated = profile != "blank"
        result[filename] = guard.LedgerRow(
            checksum=contract.release[database][filename] if populated else None,
            source_ref=source if populated else None,
            manifest_sha256=manifest if populated else None,
            evidence_kind=evidence_kind if populated else None,
            guarded=populated and evidence_kind == guard.GUARDED_EVIDENCE,
        )
    return result


def _fresh_rows(contract: guard.Contract, database: str) -> dict[str, guard.LedgerRow]:
    missing = set(guard.FRESH_BOOTSTRAP_MISSING[database])
    return {
        filename: guard.LedgerRow(None, None, None, None, False)
        for filename in contract.release[database]
        if filename not in missing
    }


def _release_attestation(
    contract: guard.Contract, tree_sha256: str
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "deploy_ref": contract.candidate_ref,
        "tag": f"v{contract.release_version}",
        "artifact_uri": (
            "gs://omega-release-artifacts/deploy-artifacts/"
            f"{contract.candidate_ref}/repo.tar.gz"
        ),
        "artifact_generation": "123456789",
        "artifact_metageneration": "1",
        "artifact_size_bytes": 4096,
        "artifact_sha256": "a" * 64,
        "artifact_crc32c": "AAAAAA==",
        "artifact_md5": "AAAAAAAAAAAAAAAAAAAAAA==",
        "version": contract.release_version,
        "tree_sha256": tree_sha256,
        "installed_at": "2026-08-12T01:02:03.456789+00:00",
    }


def test_release_attestation_payload_binds_exact_artifact_and_tree() -> None:
    contract = guard._validate_contract(_args())
    tree_sha256 = "b" * 64
    guard._validate_release_attestation_payload(
        _release_attestation(contract, tree_sha256), contract, tree_sha256
    )


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("schema_version", True, "schema version"),
        ("deploy_ref", "1" * 40, "exact deploy_ref"),
        ("tag", "v1.45.206-beta", "exact tag"),
        ("version", "1.45.206-beta", "exact version"),
        ("tree_sha256", "c" * 64, "exact tree_sha256"),
        ("artifact_uri", "gs://bucket/releases/../other.tar.gz", "artifact URI"),
        ("artifact_uri", "gs://bucket/object?generation=1", "artifact URI"),
        ("artifact_generation", "0", "artifact generation"),
        ("artifact_metageneration", "0", "artifact metageneration"),
        ("artifact_size_bytes", True, "artifact size"),
        ("artifact_size_bytes", 0, "artifact size"),
        ("artifact_sha256", "A" * 64, "artifact hash"),
        ("artifact_crc32c", "invalid", "artifact CRC32C"),
        ("artifact_md5", "invalid", "artifact MD5"),
        ("installed_at", "2026-08-12T01:02:03Z", "canonical UTC"),
        ("installed_at", "2999-08-12T01:02:03.000000+00:00", "future"),
    ],
)
def test_release_attestation_rejects_each_identity_drift(
    key: str, value: object, message: str
) -> None:
    contract = guard._validate_contract(_args())
    tree_sha256 = "b" * 64
    payload = _release_attestation(contract, tree_sha256)
    payload[key] = value
    with pytest.raises(SystemExit, match=message):
        guard._validate_release_attestation_payload(payload, contract, tree_sha256)


def test_release_attestation_rejects_extra_and_duplicate_keys() -> None:
    contract = guard._validate_contract(_args())
    tree_sha256 = "b" * 64
    payload = _release_attestation(contract, tree_sha256)
    payload["unexpected"] = "value"
    with pytest.raises(SystemExit, match="keys are not exact"):
        guard._validate_release_attestation_payload(payload, contract, tree_sha256)
    with pytest.raises(SystemExit, match="duplicate JSON key"):
        guard._decode_unique_json_object(
            b'{"schema_version":1,"schema_version":1}', "release attestation"
        )


class _HTTPResponse:
    def __init__(self, payload: dict[str, object], *, metadata: bool = False) -> None:
        self._raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.headers = {"Metadata-Flavor": "Google"} if metadata else {}

    def __enter__(self) -> "_HTTPResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, maximum: int) -> bytes:
        return self._raw[:maximum]


class _GCPOpener:
    def __init__(self, attestation: dict[str, object]) -> None:
        self.attestation = attestation
        self.urls: list[str] = []

    def open(self, request: object, *, timeout: int) -> _HTTPResponse:
        del timeout
        url = request.full_url
        self.urls.append(url)
        if url.startswith("http://metadata.google.internal/"):
            return _HTTPResponse(
                {
                    "access_token": "ya29." + "a" * 40,
                    "expires_in": 3599,
                    "token_type": "Bearer",
                },
                metadata=True,
            )
        return _HTTPResponse(
            {
                "bucket": "omega-release-artifacts",
                "name": (
                    "deploy-artifacts/" f"{self.attestation['deploy_ref']}/repo.tar.gz"
                ),
                "generation": self.attestation["artifact_generation"],
                "metageneration": self.attestation["artifact_metageneration"],
                "size": str(self.attestation["artifact_size_bytes"]),
                "metadata": {
                    "omega-artifact-sha256": self.attestation["artifact_sha256"],
                    "omega-deploy-ref": self.attestation["deploy_ref"],
                },
                "crc32c": self.attestation["artifact_crc32c"],
                "md5Hash": self.attestation["artifact_md5"],
            }
        )


def test_live_gcs_generation_matches_release_attestation() -> None:
    contract = guard._validate_contract(_args())
    attestation = _release_attestation(contract, "b" * 64)
    opener = _GCPOpener(attestation)
    receipt = guard._verify_live_gcs_release_object(attestation, opener=opener)
    assert receipt == {
        "artifact_uri": attestation["artifact_uri"],
        "artifact_generation": attestation["artifact_generation"],
        "artifact_metageneration": attestation["artifact_metageneration"],
        "artifact_sha256": attestation["artifact_sha256"],
    }
    assert len(opener.urls) == 2
    assert (
        f"deploy-artifacts%2F{contract.candidate_ref}%2Frepo.tar.gz" in opener.urls[1]
    )
    assert "generation=123456789" in opener.urls[1]


def test_live_gcs_generation_rejects_metadata_drift() -> None:
    contract = guard._validate_contract(_args())
    attestation = _release_attestation(contract, "b" * 64)
    opener = _GCPOpener(attestation)
    opener.attestation = {**attestation, "artifact_metageneration": "2"}
    with pytest.raises(SystemExit, match="differs from the release attestation"):
        guard._verify_live_gcs_release_object(attestation, opener=opener)


@pytest.mark.parametrize(
    ("mode", "uid", "gid", "size"),
    [
        (0o100600, 0, 0, 100),
        (0o120400, 0, 0, 100),
        (0o100400, 501, 0, 100),
        (0o100400, 0, 20, 100),
        (0o100400, 0, 0, 0),
        (0o100400, 0, 0, guard.MAX_RELEASE_ATTESTATION_BYTES + 1),
    ],
)
def test_release_attestation_rejects_mode_link_owner_and_size_drift(
    mode: int, uid: int, gid: int, size: int
) -> None:
    info = SimpleNamespace(st_mode=mode, st_uid=uid, st_gid=gid, st_size=size)
    with pytest.raises(SystemExit, match="root:root mode 0400 and bounded"):
        guard._validate_release_attestation_file_info(info)


def test_docker_mount_contract_requires_exact_read_only_bind(tmp_path: Path) -> None:
    source = tmp_path / "init"
    source.mkdir()
    exact = json.dumps(
        [
            {
                "Type": "bind",
                "Source": str(source),
                "Destination": "/docker-entrypoint-initdb.d",
                "RW": False,
            }
        ]
    ).encode()
    guard._validate_read_only_mounts(exact, source, "/docker-entrypoint-initdb.d")
    writable = exact.replace(b'"RW": false', b'"RW": true')
    with pytest.raises(SystemExit, match="not the exact read-only"):
        guard._validate_read_only_mounts(
            writable, source, "/docker-entrypoint-initdb.d"
        )


def _database_target_payload(init_source: Path) -> dict[str, object]:
    image_id = "sha256:" + "a" * 64
    return {
        "Id": "b" * 64,
        "Name": "/mode_postgres",
        "Image": image_id,
        "RestartCount": 0,
        "State": {"Running": True, "Status": "running"},
        "Config": {
            "Image": "pgvector/pgvector:pg15",
            "Labels": {
                "com.docker.compose.project": "infra",
                "com.docker.compose.service": "postgres",
                "com.docker.compose.container-number": "1",
                "com.docker.compose.oneoff": "False",
                "com.docker.compose.image": image_id,
            },
        },
        "Mounts": [
            {
                "Type": "volume",
                "Name": "infra_postgres_data",
                "Destination": "/var/lib/postgresql/data",
                "RW": True,
            },
            {
                "Type": "bind",
                "Source": str(init_source),
                "Destination": "/docker-entrypoint-initdb.d",
                "RW": False,
            },
        ],
    }


def test_database_target_seals_container_image_project_and_volume(
    tmp_path: Path,
) -> None:
    init_source = tmp_path / "init"
    init_source.mkdir()
    payload = _database_target_payload(init_source)
    receipt = guard._validate_database_target(
        json.dumps([payload]).encode("utf-8"),
        expected_container_id="b" * 64,
        expected_service="postgres",
        expected_project="infra",
        expected_init_source=init_source,
        expected_image_reference="pgvector/pgvector:pg15",
    )
    assert receipt == {
        "container_id": "b" * 64,
        "image_id": "sha256:" + "a" * 64,
        "volume": "infra_postgres_data",
    }


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("RestartCount", 1, "container identity"),
        ("Image", "sha256:" + "c" * 64, "image or Compose identity"),
    ],
)
def test_database_target_rejects_runtime_identity_drift(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    init_source = tmp_path / "init"
    init_source.mkdir()
    payload = _database_target_payload(init_source)
    payload[field] = value
    with pytest.raises(SystemExit, match=message):
        guard._validate_database_target(
            json.dumps([payload]).encode("utf-8"),
            expected_container_id="b" * 64,
            expected_service="postgres",
            expected_project="infra",
            expected_init_source=init_source,
            expected_image_reference="pgvector/pgvector:pg15",
        )


def test_database_system_identity_is_exact() -> None:
    assert (
        guard._validate_database_system_identity(
            b"modecissions|postgres|postgres|7672755278113935396\n",
            expected_database="modecissions",
        )
        == "7672755278113935396"
    )
    with pytest.raises(SystemExit, match="database/system identifier differs"):
        guard._validate_database_system_identity(
            b"modecissions|omega_console|omega_console|7672755278113935396\n",
            expected_database="modecissions",
        )


def test_complete_manifests_are_exact_and_only_add_the_pending_set() -> None:
    contract = guard._validate_contract(_args())
    baseline_json = json.loads(contract.baseline_path.read_text(encoding="utf-8"))
    release_json = json.loads(contract.release_path.read_text(encoding="utf-8"))
    assert baseline_json["checksum_provenance"] == guard.BASELINE_PROVENANCE
    assert baseline_json["checksum_provenance"]["historical_execution_receipt"] is False
    assert release_json["evidence_contract"] == guard.RELEASE_EVIDENCE_CONTRACT
    assert len(contract.baseline["operational"]) == 198
    assert len(contract.baseline["gold"]) == 12
    assert len(contract.release["operational"]) == 201
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
    partial[filename] = guard.LedgerRow(None, None, None, None, False)
    with pytest.raises(SystemExit, match="partial/tampered"):
        guard._validate_ledger(contract, "operational", partial, require_release=False)

    unknown = _rows(contract, "gold", state="release")
    unknown["gold/99_unknown.sql"] = guard.LedgerRow(None, None, None, None, False)
    with pytest.raises(SystemExit, match="partial/tampered"):
        guard._validate_ledger(contract, "gold", unknown, require_release=False)


def test_release_filenames_with_null_evidence_are_not_blessed() -> None:
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
        contract.release["operational"][filename],
        source,
        manifest,
        guard.GUARDED_EVIDENCE,
        True,
    )
    with pytest.raises(SystemExit, match="refusing to bless"):
        guard._validate_ledger(
            contract,
            "operational",
            partial,
            require_release=False,
            allow_bootstrap_release=True,
        )


def test_only_exact_fresh_entrypoint_profiles_are_bootstrap_eligible() -> None:
    contract = guard._validate_contract(_args())
    expected_counts = {"operational": 176, "gold": 9}
    for database, count in expected_counts.items():
        rows = _fresh_rows(contract, database)
        assert len(rows) == count
        with pytest.raises(SystemExit, match="partial/tampered"):
            guard._validate_ledger(
                contract,
                database,
                rows,
                require_release=False,
            )
        assert (
            guard._validate_ledger(
                contract,
                database,
                rows,
                require_release=False,
                allow_bootstrap_release=True,
            )
            == "fresh_bootstrap"
        )

        missing_one = dict(rows)
        missing_one.pop(next(iter(missing_one)))
        with pytest.raises(SystemExit, match="partial/tampered"):
            guard._validate_ledger(
                contract,
                database,
                missing_one,
                require_release=False,
                allow_bootstrap_release=True,
            )

        tampered = dict(rows)
        filename = next(iter(tampered))
        tampered[filename] = guard.LedgerRow("0" * 64, None, None, None, False)
        with pytest.raises(SystemExit, match="checksum mismatch"):
            guard._validate_ledger(
                contract,
                database,
                tampered,
                require_release=False,
                allow_bootstrap_release=True,
            )


def test_fresh_bootstrap_sql_normalizes_without_reexecuting_migrations() -> None:
    contract = guard._validate_contract(_args())
    for database, missing_count in (("operational", 25), ("gold", 3)):
        sql = guard._render_sql(contract, database, "fresh_bootstrap")
        assert "ADD COLUMN IF NOT EXISTS checksum text" in sql
        assert "CREATE TEMP TABLE omega_expected_missing" in sql
        assert sql.count("\\i /docker-entrypoint-initdb.d/") == 0
        missing_section = sql.split("INSERT INTO omega_expected_missing VALUES", 1)[1]
        missing_section = missing_section.split(
            "INSERT INTO public.schema_migrations", 1
        )[0]
        assert missing_section.count("'baseline_expected'") == missing_count
        assert "'guarded_transaction'" not in missing_section
        assert "ALTER TABLE public.schema_migrations OWNER TO postgres" in sql
        assert "pg_write_all_data" in sql


def test_postflight_separates_expected_baseline_from_guarded_pending_set() -> None:
    contract = guard._validate_contract(_args())
    guarded = _rows(contract, "operational", state="release", profile="guarded")
    assert (
        guard._validate_ledger(
            contract,
            "operational",
            guarded,
            require_release=True,
            expected_pending_evidence=guard.GUARDED_EVIDENCE,
        )
        == "release_guarded"
    )
    assert (
        sum(row.evidence_kind == guard.BASELINE_EVIDENCE for row in guarded.values())
        == 198
    )
    assert sum(
        row.evidence_kind == guard.GUARDED_EVIDENCE for row in guarded.values()
    ) == len(guard.PENDING_OPERATIONAL)

    source_inferred = _rows(
        contract, "operational", state="release", profile="baseline"
    )
    with pytest.raises(SystemExit, match="guarded transaction receipt"):
        guard._validate_ledger(
            contract,
            "operational",
            source_inferred,
            require_release=True,
            expected_pending_evidence=guard.GUARDED_EVIDENCE,
        )
    assert (
        guard._validate_ledger(
            contract,
            "operational",
            source_inferred,
            require_release=True,
            allow_bootstrap_release=True,
            expected_pending_evidence=guard.BASELINE_EVIDENCE,
        )
        == "release_expected"
    )


def test_tampered_checksum_or_provenance_fails_before_writes() -> None:
    contract = guard._validate_contract(_args())
    rows = _rows(contract, "operational", state="baseline", profile="baseline")
    filename = next(iter(rows))
    original = rows[filename]
    rows[filename] = guard.LedgerRow(
        checksum="0" * 64,
        source_ref=original.source_ref,
        manifest_sha256=original.manifest_sha256,
        evidence_kind=original.evidence_kind,
        guarded=original.guarded,
    )
    with pytest.raises(SystemExit, match="checksum mismatch"):
        guard._validate_ledger(contract, "operational", rows, require_release=False)

    rows = _rows(contract, "gold", state="release", profile="baseline")
    filename = next(iter(rows))
    original = rows[filename]
    rows[filename] = guard.LedgerRow(
        checksum=original.checksum,
        source_ref="2" * 40,
        manifest_sha256=original.manifest_sha256,
        evidence_kind=original.evidence_kind,
        guarded=original.guarded,
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
                "evidence_kind": None,
                "guarded": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="checksum is invalid"):
        guard._load_ledger(path, "operational")


def test_partial_evidence_profile_fails_closed() -> None:
    contract = guard._validate_contract(_args())
    rows = _rows(contract, "gold", state="release", profile="baseline")
    filename = next(iter(rows))
    original = rows[filename]
    rows[filename] = guard.LedgerRow(
        checksum=original.checksum,
        source_ref=original.source_ref,
        manifest_sha256=original.manifest_sha256,
        evidence_kind=None,
        guarded=False,
    )
    with pytest.raises(SystemExit, match="neither uniformly blank nor exact"):
        guard._validate_ledger(contract, "gold", rows, require_release=False)
    with pytest.raises(SystemExit, match="neither uniformly blank nor exact"):
        guard._validate_ledger(contract, "gold", rows, require_release=True)
    sql = guard._render_sql(contract, "gold", "baseline_expected")
    assert "checksum_evidence_kind" in sql
    assert "checksum_guarded_at" in sql


def test_retry_accepts_gold_expected_with_operational_still_at_baseline() -> None:
    contract = guard._validate_contract(_args())
    operational = _rows(contract, "operational", state="baseline")
    gold = _rows(contract, "gold", state="release", profile="baseline")
    assert (
        guard._validate_ledger(
            contract, "operational", operational, require_release=False
        )
        == "baseline"
    )
    assert (
        guard._validate_ledger(contract, "gold", gold, require_release=False)
        == "baseline_expected"
    )


def test_pending_set_is_one_psql_transaction_without_nested_terminators() -> None:
    contract = guard._validate_contract(_args())
    sql = guard._render_sql(contract, "operational", "baseline")
    assert sql.splitlines()[1] == "SET default_transaction_read_only=off;"
    assert sql.splitlines()[2] == "BEGIN;"
    assert sql.splitlines()[3] == "SET TRANSACTION READ WRITE;"
    assert "SET LOCAL lock_timeout = '30s';" in sql
    assert "SET LOCAL statement_timeout = '15min';" in sql
    assert "SET LOCAL idle_in_transaction_session_timeout = '60s';" in sql
    assert sql.index("SET LOCAL lock_timeout") < sql.index("pg_advisory_xact_lock")
    assert sql.rstrip().endswith("COMMIT;")
    assert sql.count("\nBEGIN;") == 1
    assert sql.count("\nCOMMIT;") == 1
    assert "\\i /docker-entrypoint-initdb.d/" not in sql
    first = sql.index(
        f"BEGIN verified immutable snapshot: {guard.PENDING_OPERATIONAL[0]}"
    )
    positions = [
        sql.index(f"BEGIN verified immutable snapshot: {filename}")
        for filename in guard.PENDING_OPERATIONAL
    ]
    assert positions == sorted(positions)
    assert first == positions[0] < positions[-1] < sql.rindex("COMMIT;")
    assert sql.count("'guarded_transaction', clock_timestamp()") == len(
        guard.PENDING_OPERATIONAL
    )
    assert "checksum_evidence_kind" in sql
    assert "checksum_guarded_at" in sql
    terminator = re.compile(
        r"^\s*(?:BEGIN|COMMIT|ROLLBACK|START\s+TRANSACTION)\s*;",
        flags=re.IGNORECASE | re.MULTILINE,
    )
    for filename in guard.PENDING_OPERATIONAL:
        source = (ROOT / "infra" / "init" / filename).read_text(encoding="utf-8")
        assert not terminator.search(source), filename


def test_pending_snapshot_segments_are_exact_source_bytes() -> None:
    contract = guard._validate_contract(_args())
    rendered = guard._render_sql_bytes(contract, "operational", "baseline")
    for filename in guard.PENDING_OPERATIONAL:
        begin = f"-- BEGIN verified immutable snapshot: {filename}\n".encode("utf-8")
        end = f"-- END verified immutable snapshot: {filename}".encode("utf-8")
        begin_at = rendered.index(begin) + len(begin)
        end_at = rendered.index(end, begin_at)
        embedded = rendered[begin_at:end_at]
        source = (ROOT / "infra" / "init" / filename).read_bytes()
        assert embedded == source
        assert (
            hashlib.sha256(embedded).hexdigest()
            == contract.release["operational"][filename]
        )


def test_render_cli_is_byte_exact_under_hostile_text_encoding(tmp_path: Path) -> None:
    contract = guard._validate_contract(_args())
    plan = tmp_path / "plan.json"
    guard._write_plan(contract, "baseline", "baseline_expected", plan)
    environment = os.environ.copy()
    environment.update(
        {
            "LC_ALL": "C",
            "LANG": "C",
            "PYTHONIOENCODING": "ascii:ignore",
        }
    )
    rendered = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "migration_guard.py"),
            "render-sql",
            "--plan",
            str(plan),
            "--database",
            "operational",
        ],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert rendered.returncode == 0, rendered.stderr.decode(errors="replace")
    assert rendered.stdout == guard._render_sql_bytes(
        contract, "operational", "baseline"
    )


def test_pending_sql_snapshot_rehashes_the_bytes_it_embeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract = guard._validate_contract(_args())
    changed = tmp_path / guard.PENDING_OPERATIONAL[0]
    changed.write_text("SELECT 1;\n", encoding="utf-8")
    original = guard._migration_path

    def migration_path(database: str, filename: str) -> Path:
        if database == "operational" and filename == guard.PENDING_OPERATIONAL[0]:
            return changed
        return original(database, filename)

    monkeypatch.setattr(guard, "_migration_path", migration_path)
    with pytest.raises(SystemExit, match="changed while snapshotting"):
        guard._render_sql(contract, "operational", "baseline")


def test_shell_orders_two_database_preflight_then_gold_then_operational() -> None:
    script = (ROOT / "scripts" / "apply_db_migrations.sh").read_text(encoding="utf-8")
    preflight = script.index('python3 "$GUARD" preflight')
    gold = script.index('render-sql --plan "$PLAN" --database gold')
    operational = script.index('render-sql --plan "$PLAN" --database operational')
    assert script.index("dump_operational_ledger >") < preflight
    assert script.index("dump_gold_ledger >") < preflight
    assert preflight < gold < operational
    gold_apply = script.index('"${PSQL_GOLD[@]}" -f - < "$GOLD_SQL"')
    assert operational < gold_apply
    assert "OMEGA_MIGRATION_REQUIRE_EXPLICIT_CONTRACT" in script
    assert "bootstrap source-inferred ledger evidence is forbidden" in script
    assert "verify-read-only-mount" in script
    assert "fsync-file" in script
    assert "pwd -P" in script


def test_plan_contains_no_unreviewed_fields(tmp_path: Path) -> None:
    contract = guard._validate_contract(_args())
    plan = tmp_path / "plan.json"
    guard._write_plan(contract, "baseline", "baseline_expected", plan)
    value = json.loads(plan.read_text(encoding="utf-8"))
    assert value["candidate_ref"] == CANDIDATE_REF
    assert value["operational_state"] == "baseline"
    assert value["gold_state"] == "baseline_expected"
    assert value["schema_version"] == 2
    assert value["expected_pending_evidence"] == guard.GUARDED_EVIDENCE

    fresh_plan = tmp_path / "fresh-plan.json"
    guard._write_plan(contract, "fresh_bootstrap", "fresh_bootstrap", fresh_plan)
    fresh_value = json.loads(fresh_plan.read_text(encoding="utf-8"))
    assert fresh_value["expected_pending_evidence"] == guard.BASELINE_EVIDENCE
    guard._validate_plan(fresh_value, expected_contract=contract)

    fresh_value["expected_pending_evidence"] = guard.GUARDED_EVIDENCE
    with pytest.raises(SystemExit, match="not derived from preflight state"):
        guard._validate_plan(fresh_value, expected_contract=contract)


def test_ledger_authority_assertion_covers_acl_membership_and_postgres() -> None:
    sql = "\n".join(guard._ledger_authority_assertion_lines())
    assert "tableowner" in sql
    assert "sequenceowner" in sql
    assert "has_table_privilege('postgres'" in sql
    assert "has_sequence_privilege('postgres'" in sql
    assert "aclexplode" in sql
    assert "pg_write_all_data" in sql
    assert "pg_read_all_data" in sql
    assert "pg_has_role(oid, 'postgres', 'MEMBER')" in sql
    assert "rolsuper OR rolcreaterole" in sql
    assert "has_column_privilege('omega_console'" in sql
    assert "pg_attribute attribute" in sql
    assert "attribute.attacl" in sql
    assert "attname NOT IN ('filename','applied_at','checksum')" in sql
    rendered = guard._render_sql(
        guard._validate_contract(_args()), "operational", "baseline"
    )
    assert "GRANT SELECT (filename, applied_at, checksum)" in rendered
    assert "REVOKE ALL PRIVILEGES (%I) ON TABLE" in rendered
    assert "('postgres', 'pg_write_all_data', 'pg_read_all_data')" in rendered


def test_control_room_ci_runs_and_fail_closes_the_real_migration_gate() -> None:
    workflow = (
        ROOT / ".github" / "workflows" / "control-room-postgres-rls.yml"
    ).read_text(encoding="utf-8")
    unit = workflow.split("- name: Run immutable migration release unit gate", 1)[1]
    unit = unit.split("- name: Run immutable migration release live gate", 1)[0]
    assert "continue-on-error" not in unit
    for path in (
        "tests/test_migration_release_guard.py",
        "tests/test_successfactors_apps_seed_sync.py",
        "tests/test_apply_db_migrations_script.py",
    ):
        assert path in unit
    assert "tests/test_migration_release_guard_live.py" not in unit
    live = workflow.split("- name: Run immutable migration release live gate", 1)[1]
    live = live.split("- name: Run live PostgreSQL/RLS tests", 1)[0]
    assert "continue-on-error" not in live
    assert "tests/test_migration_release_guard_live.py" in live
    assert "--junitxml=/tmp/migration-release-guard-live.xml" in live
    assert (
        'verify_junit("/tmp/migration-release-guard.xml", minimum=53, '
        'label="Migration release guard")' in workflow
    )
    assert (
        'verify_junit("/tmp/migration-release-guard-live.xml", minimum=3, '
        'label="Live migration release guard")' in workflow
    )
