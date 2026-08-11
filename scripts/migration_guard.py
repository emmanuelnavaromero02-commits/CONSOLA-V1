#!/usr/bin/env python3
"""Fail-closed migration manifest, ledger, and SQL transaction guard."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BASELINE_REF = "6b12883c5b5ea0537120279ccbee4947137998a2"
RELEASE_VERSION = "1.45.207-beta"
BASELINE_NAME = f"gcp-live-{BASELINE_REF}.json"
RELEASE_NAME = f"v{RELEASE_VERSION}.json"
PINNED_BASELINE_MANIFEST_SHA256 = (
    "b3b984fb88f48a5d75e172196e1981a45b54aba90eb3365d7e5afbcdac929221"
)
PINNED_RELEASE_MANIFEST_SHA256 = (
    "aefda14599840b6ee419eb6b76878478e6be64e2706a80363d7cfac19cdf79a6"
)
PENDING_OPERATIONAL = (
    "99zzt_analytic_app_dataset_grants.sql",
    "99zzu_analytic_app_manifest_registry.sql",
)

_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OP_NAME = re.compile(r"^[0-9]{2}[0-9a-z_]*\.sql$")
_GOLD_NAME = re.compile(r"^gold/[0-9]{2}[0-9a-z_]*\.sql$")


def _die(message: str) -> None:
    raise SystemExit(f"migration guard: {message}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _die(f"cannot read {path}: {exc}")
    if not isinstance(value, dict):
        _die(f"{path} must contain one JSON object")
    return value


def _manifest_map(
    manifest: dict[str, Any], database: str, *, expected_count: int
) -> dict[str, str]:
    databases = manifest.get("databases")
    if not isinstance(databases, dict) or set(databases) != {"operational", "gold"}:
        _die("manifest database keys must be exactly operational and gold")
    migrations = databases.get(database)
    if not isinstance(migrations, dict) or len(migrations) != expected_count:
        _die(
            f"{database} manifest count is not exactly {expected_count}: "
            f"{len(migrations) if isinstance(migrations, dict) else 'invalid'}"
        )
    matcher = _GOLD_NAME if database == "gold" else _OP_NAME
    for filename, checksum in migrations.items():
        if not isinstance(filename, str) or not matcher.fullmatch(filename):
            _die(f"unsafe {database} migration filename: {filename!r}")
        if not isinstance(checksum, str) or not _SHA256.fullmatch(checksum):
            _die(f"invalid checksum for {filename}")
    return dict(migrations)


@dataclass(frozen=True)
class Contract:
    old_ref: str
    candidate_ref: str
    release_version: str
    baseline_path: Path
    release_path: Path
    baseline_sha256: str
    release_sha256: str
    baseline: dict[str, dict[str, str]]
    release: dict[str, dict[str, str]]

    def as_plan_fields(self) -> dict[str, Any]:
        return {
            "old_ref": self.old_ref,
            "candidate_ref": self.candidate_ref,
            "release_version": self.release_version,
            "baseline_path": str(self.baseline_path),
            "release_path": str(self.release_path),
            "baseline_sha256": self.baseline_sha256,
            "release_sha256": self.release_sha256,
        }


def _migration_path(database: str, filename: str) -> Path:
    if database == "gold":
        return ROOT / "infra" / "init_gold" / filename.removeprefix("gold/")
    return ROOT / "infra" / "init" / filename


def _validate_contract(args: argparse.Namespace) -> Contract:
    if not _SHA40.fullmatch(args.old_ref or ""):
        _die("OLD_REF must be one exact lowercase 40-character SHA")
    if not _SHA40.fullmatch(args.candidate_ref or ""):
        _die("candidate ref must be one exact lowercase 40-character SHA")
    if args.release_version != RELEASE_VERSION:
        _die(f"release version must be exactly {RELEASE_VERSION}")
    if args.old_ref != BASELINE_REF:
        _die(f"old ref must match the audited GCP baseline {BASELINE_REF}")

    manifest_root = (ROOT / "infra" / "migrations" / "manifests").resolve()
    baseline_path = Path(args.baseline_manifest).resolve()
    release_path = Path(args.release_manifest).resolve()
    if baseline_path != manifest_root / BASELINE_NAME:
        _die("baseline manifest path is not the canonical release artifact path")
    if release_path != manifest_root / RELEASE_NAME:
        _die("release manifest path is not the canonical release artifact path")
    if args.baseline_manifest_sha256 != PINNED_BASELINE_MANIFEST_SHA256:
        _die("baseline manifest hash does not match the reviewed runner pin")
    if args.release_manifest_sha256 != PINNED_RELEASE_MANIFEST_SHA256:
        _die("release manifest hash does not match the reviewed runner pin")
    if _sha256(baseline_path) != args.baseline_manifest_sha256:
        _die("baseline manifest bytes do not match the supplied hash")
    if _sha256(release_path) != args.release_manifest_sha256:
        _die("release manifest bytes do not match the supplied hash")

    baseline_json = _load_json(baseline_path)
    release_json = _load_json(release_path)
    if baseline_json.get("schema_version") != 1:
        _die("baseline manifest schema version is not 1")
    if baseline_json.get("kind") != "omega_database_migration_baseline":
        _die("baseline manifest kind is invalid")
    if baseline_json.get("environment") != "gcp-canonical":
        _die("baseline environment is not gcp-canonical")
    if baseline_json.get("source_ref") != args.old_ref:
        _die("baseline manifest is not anchored to OLD_REF")
    if release_json.get("schema_version") != 1:
        _die("release manifest schema version is not 1")
    if release_json.get("kind") != "omega_database_migration_release_lock":
        _die("release manifest kind is invalid")
    if release_json.get("release_version") != args.release_version:
        _die("release manifest version does not match VERSION")
    if release_json.get("candidate_ref_binding") != "runtime_exact_40_hex_sha":
        _die("release manifest does not require an exact runtime candidate SHA")
    if release_json.get("baseline_source_ref") != args.old_ref:
        _die("release manifest baseline does not match OLD_REF")
    if release_json.get("allowed_new_migrations") != {
        "operational": list(PENDING_OPERATIONAL),
        "gold": [],
    }:
        _die("release manifest pending set is not the audited 99zzt/99zzu pair")

    baseline = {
        "operational": _manifest_map(baseline_json, "operational", expected_count=198),
        "gold": _manifest_map(baseline_json, "gold", expected_count=12),
    }
    release = {
        "operational": _manifest_map(release_json, "operational", expected_count=200),
        "gold": _manifest_map(release_json, "gold", expected_count=12),
    }

    for database in ("operational", "gold"):
        changed = sorted(
            filename
            for filename, checksum in baseline[database].items()
            if release[database].get(filename) != checksum
        )
        missing = sorted(set(baseline[database]) - set(release[database]))
        added = sorted(set(release[database]) - set(baseline[database]))
        allowed = sorted(PENDING_OPERATIONAL if database == "operational" else ())
        if changed or missing or added != allowed:
            _die(
                f"{database} release drift: changed={changed} missing={missing} "
                f"added={added} allowed={allowed}"
            )
        disk_names = {
            path.name if database == "operational" else f"gold/{path.name}"
            for path in (
                ROOT
                / ("infra/init" if database == "operational" else "infra/init_gold")
            ).glob(
                "[0-9][0-9]*_*.sql" if database == "operational" else "[0-9][0-9]_*.sql"
            )
        }
        if disk_names != set(release[database]):
            _die(f"{database} migration directory is not the release manifest set")
        for filename, expected in release[database].items():
            migration = _migration_path(database, filename)
            if _sha256(migration) != expected:
                _die(f"release migration bytes differ from lock: {filename}")
            if database == "operational" and filename in PENDING_OPERATIONAL:
                text = migration.read_text(encoding="utf-8")
                if re.search(
                    r"^\s*(?:BEGIN|COMMIT|ROLLBACK|START\s+TRANSACTION)\s*;",
                    text,
                    flags=re.IGNORECASE | re.MULTILINE,
                ):
                    _die(
                        f"pending migration contains a transaction terminator: {filename}"
                    )

    version_path = ROOT / "VERSION"
    if version_path.read_text(encoding="utf-8").strip() != args.release_version:
        _die("VERSION does not match the release migration lock")

    return Contract(
        old_ref=args.old_ref,
        candidate_ref=args.candidate_ref,
        release_version=args.release_version,
        baseline_path=baseline_path,
        release_path=release_path,
        baseline_sha256=args.baseline_manifest_sha256,
        release_sha256=args.release_manifest_sha256,
        baseline=baseline,
        release=release,
    )


@dataclass(frozen=True)
class LedgerRow:
    checksum: str | None
    source_ref: str | None
    manifest_sha256: str | None
    attested: bool


def _load_ledger(path: Path, database: str) -> dict[str, LedgerRow]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        _die(f"cannot read {database} ledger: {exc}")
    if not lines:
        _die(f"{database} schema_migrations ledger is empty")
    result: dict[str, LedgerRow] = {}
    matcher = _GOLD_NAME if database == "gold" else _OP_NAME
    for number, line in enumerate(lines, 1):
        try:
            fields = json.loads(line)
        except json.JSONDecodeError:
            _die(f"{database} ledger line {number} is malformed")
        if not isinstance(fields, dict) or set(fields) != {
            "filename",
            "checksum",
            "source_ref",
            "manifest_sha256",
            "attested",
        }:
            _die(f"{database} ledger line {number} has unexpected fields")
        filename = fields["filename"]
        checksum_value = fields["checksum"]
        source_value = fields["source_ref"]
        manifest_value = fields["manifest_sha256"]
        attested = fields["attested"]
        if not isinstance(filename, str):
            _die(f"{database} ledger filename has an invalid type")
        if not matcher.fullmatch(filename) or filename in result:
            _die(f"{database} ledger filename is unsafe or duplicated: {filename!r}")
        if checksum_value is not None and (
            not isinstance(checksum_value, str) or not _SHA256.fullmatch(checksum_value)
        ):
            _die(f"{database} ledger checksum is invalid: {filename}")
        if source_value is not None and (
            not isinstance(source_value, str) or not _SHA40.fullmatch(source_value)
        ):
            _die(f"{database} ledger source ref is invalid: {filename}")
        if manifest_value is not None and (
            not isinstance(manifest_value, str) or not _SHA256.fullmatch(manifest_value)
        ):
            _die(f"{database} ledger manifest hash is invalid: {filename}")
        if not isinstance(attested, bool):
            _die(f"{database} ledger attestation flag is invalid: {filename}")
        if checksum_value is None and (
            source_value is not None or manifest_value is not None or attested
        ):
            _die(f"{database} ledger has provenance without a checksum: {filename}")
        result[filename] = LedgerRow(
            checksum=checksum_value,
            source_ref=source_value,
            manifest_sha256=manifest_value,
            attested=attested,
        )
    return result


def _expected_provenance(
    contract: Contract, database: str, filename: str
) -> tuple[str, str]:
    if database == "operational" and filename in PENDING_OPERATIONAL:
        return contract.candidate_ref, contract.release_sha256
    return contract.old_ref, contract.baseline_sha256


def _validate_ledger(
    contract: Contract,
    database: str,
    rows: dict[str, LedgerRow],
    *,
    require_release: bool,
    allow_bootstrap_release: bool = False,
) -> str:
    names = set(rows)
    baseline_names = set(contract.baseline[database])
    release_names = set(contract.release[database])
    if names == release_names:
        state = "release"
    elif not require_release and names == baseline_names:
        state = "baseline"
    else:
        unknown = sorted(names - release_names)
        missing_baseline = sorted(baseline_names - names)
        partial_pending = sorted(names & set(PENDING_OPERATIONAL))
        _die(
            f"{database} ledger is partial/tampered: unknown={unknown} "
            f"missing_baseline={missing_baseline} pending_present={partial_pending}"
        )

    if database == "operational" and state == "release" and not require_release:
        fully_attested = all(
            row.checksum == contract.release[database][filename]
            and row.source_ref == _expected_provenance(contract, database, filename)[0]
            and row.manifest_sha256
            == _expected_provenance(contract, database, filename)[1]
            and row.attested
            for filename, row in rows.items()
        )
        fully_unattested = all(
            row.checksum is None
            and row.source_ref is None
            and row.manifest_sha256 is None
            and not row.attested
            for row in rows.values()
        )
        if fully_attested:
            pass
        elif allow_bootstrap_release and fully_unattested:
            state = "bootstrap_release"
        else:
            _die(
                "operational release filenames exist without one complete "
                "candidate attestation; refusing to bless a partial/pending pair"
            )

    expected = contract.release[database]
    for filename, row in rows.items():
        if row.checksum is not None and row.checksum != expected[filename]:
            _die(f"{database} ledger checksum mismatch: {filename}")
        expected_ref, expected_manifest = _expected_provenance(
            contract, database, filename
        )
        if row.source_ref is not None and row.source_ref != expected_ref:
            _die(f"{database} ledger source provenance mismatch: {filename}")
        if row.manifest_sha256 is not None and row.manifest_sha256 != expected_manifest:
            _die(f"{database} ledger manifest provenance mismatch: {filename}")
        if require_release and (
            row.checksum is None
            or row.source_ref != expected_ref
            or row.manifest_sha256 != expected_manifest
            or not row.attested
        ):
            _die(f"{database} ledger is not fully attested: {filename}")
    return state


def _write_plan(
    contract: Contract,
    operational_state: str,
    gold_state: str,
    path: Path,
) -> None:
    value = {
        "schema_version": 1,
        **contract.as_plan_fields(),
        "operational_state": operational_state,
        "gold_state": gold_state,
    }
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _args_from_plan(plan: dict[str, Any]) -> argparse.Namespace:
    required = {
        "old_ref",
        "candidate_ref",
        "release_version",
        "baseline_path",
        "release_path",
        "baseline_sha256",
        "release_sha256",
    }
    if plan.get("schema_version") != 1 or not required.issubset(plan):
        _die("migration plan is malformed")
    return argparse.Namespace(
        old_ref=plan["old_ref"],
        candidate_ref=plan["candidate_ref"],
        release_version=plan["release_version"],
        baseline_manifest=plan["baseline_path"],
        release_manifest=plan["release_path"],
        baseline_manifest_sha256=plan["baseline_sha256"],
        release_manifest_sha256=plan["release_sha256"],
    )


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _values_sql(contract: Contract, database: str, filenames: list[str]) -> str:
    rows = []
    for filename in filenames:
        source_ref, manifest_sha = _expected_provenance(contract, database, filename)
        rows.append(
            "("
            + ", ".join(
                _sql_literal(value)
                for value in (
                    filename,
                    contract.release[database][filename],
                    source_ref,
                    manifest_sha,
                )
            )
            + ")"
        )
    return ",\n  ".join(rows)


def _render_sql(contract: Contract, database: str, state: str) -> str:
    if database not in {"operational", "gold"}:
        _die("database must be operational or gold")
    if state not in {"baseline", "release", "bootstrap_release"}:
        _die("plan database state is invalid")
    if database == "gold" and state != "release":
        # Baseline and release Gold sets are identical; normalize the label.
        state = "release"

    pre_names = sorted(
        contract.baseline[database]
        if state == "baseline"
        else contract.release[database]
    )
    final_names = sorted(contract.release[database])
    pre_values = _values_sql(contract, database, pre_names)
    final_values = _values_sql(contract, database, final_names)
    lock_key = "1" if database == "operational" else "2"
    parts = [
        "\\set ON_ERROR_STOP on",
        "BEGIN;",
        f"SELECT pg_advisory_xact_lock(145207, {lock_key});",
        "CREATE TEMP TABLE omega_expected_pre (",
        "  filename text PRIMARY KEY, checksum text NOT NULL,",
        "  source_ref text NOT NULL, manifest_sha256 text NOT NULL",
        ") ON COMMIT DROP;",
        "INSERT INTO omega_expected_pre VALUES",
        f"  {pre_values};",
        "DO $omega_preflight$",
        "BEGIN",
        "  IF EXISTS (SELECT filename FROM public.schema_migrations EXCEPT SELECT filename FROM omega_expected_pre)",
        "     OR EXISTS (SELECT filename FROM omega_expected_pre EXCEPT SELECT filename FROM public.schema_migrations) THEN",
        "    RAISE EXCEPTION 'schema_migrations changed after two-database preflight';",
        "  END IF;",
        "  IF EXISTS (",
        "    SELECT 1 FROM public.schema_migrations sm",
        "    JOIN omega_expected_pre e USING (filename)",
        "    WHERE sm.checksum IS NOT NULL AND sm.checksum <> e.checksum",
        "  ) THEN",
        "    RAISE EXCEPTION 'schema_migrations checksum changed after preflight';",
        "  END IF;",
        "END",
        "$omega_preflight$;",
        "ALTER TABLE public.schema_migrations",
        "  ADD COLUMN IF NOT EXISTS checksum_source_ref text,",
        "  ADD COLUMN IF NOT EXISTS checksum_manifest_sha256 text,",
        "  ADD COLUMN IF NOT EXISTS checksum_attested_at timestamptz;",
        "DO $omega_provenance$",
        "BEGIN",
        "  IF EXISTS (",
        "    SELECT 1 FROM public.schema_migrations sm",
        "    JOIN omega_expected_pre e USING (filename)",
        "    WHERE (sm.checksum_source_ref IS NOT NULL AND sm.checksum_source_ref <> e.source_ref)",
        "       OR (sm.checksum_manifest_sha256 IS NOT NULL AND sm.checksum_manifest_sha256 <> e.manifest_sha256)",
        "  ) THEN",
        "    RAISE EXCEPTION 'schema_migrations provenance changed after preflight';",
        "  END IF;",
        "END",
        "$omega_provenance$;",
        "UPDATE public.schema_migrations sm",
        "   SET checksum = e.checksum,",
        "       checksum_source_ref = e.source_ref,",
        "       checksum_manifest_sha256 = e.manifest_sha256,",
        "       checksum_attested_at = COALESCE(sm.checksum_attested_at, clock_timestamp())",
        "  FROM omega_expected_pre e",
        " WHERE sm.filename = e.filename",
        "   AND ((sm.checksum, sm.checksum_source_ref, sm.checksum_manifest_sha256)",
        "        IS DISTINCT FROM (e.checksum, e.source_ref, e.manifest_sha256)",
        "        OR sm.checksum_attested_at IS NULL);",
    ]

    if database == "operational" and state == "baseline":
        for filename in PENDING_OPERATIONAL:
            checksum = contract.release[database][filename]
            parts.extend(
                [
                    f"\\echo [migrate] apply {filename}",
                    f"\\i /docker-entrypoint-initdb.d/{filename}",
                    "INSERT INTO public.schema_migrations",
                    "  (filename, applied_at, checksum, checksum_source_ref,",
                    "   checksum_manifest_sha256, checksum_attested_at)",
                    "VALUES (",
                    f"  {_sql_literal(filename)}, clock_timestamp(), {_sql_literal(checksum)},",
                    f"  {_sql_literal(contract.candidate_ref)}, {_sql_literal(contract.release_sha256)},",
                    "  clock_timestamp()",
                    ")",
                    "ON CONFLICT (filename) DO UPDATE SET",
                    "  checksum = EXCLUDED.checksum,",
                    "  checksum_source_ref = EXCLUDED.checksum_source_ref,",
                    "  checksum_manifest_sha256 = EXCLUDED.checksum_manifest_sha256,",
                    "  checksum_attested_at = EXCLUDED.checksum_attested_at;",
                ]
            )

    parts.extend(
        [
            "CREATE TEMP TABLE omega_expected_final (",
            "  filename text PRIMARY KEY, checksum text NOT NULL,",
            "  source_ref text NOT NULL, manifest_sha256 text NOT NULL",
            ") ON COMMIT DROP;",
            "INSERT INTO omega_expected_final VALUES",
            f"  {final_values};",
            "DO $omega_final$",
            "BEGIN",
            "  IF EXISTS (SELECT filename FROM public.schema_migrations EXCEPT SELECT filename FROM omega_expected_final)",
            "     OR EXISTS (SELECT filename FROM omega_expected_final EXCEPT SELECT filename FROM public.schema_migrations) THEN",
            "    RAISE EXCEPTION 'final schema_migrations filename set is not exact';",
            "  END IF;",
            "  IF EXISTS (",
            "    SELECT 1 FROM public.schema_migrations sm",
            "    JOIN omega_expected_final e USING (filename)",
            "    WHERE sm.checksum IS DISTINCT FROM e.checksum",
            "       OR sm.checksum_source_ref IS DISTINCT FROM e.source_ref",
            "       OR sm.checksum_manifest_sha256 IS DISTINCT FROM e.manifest_sha256",
            "       OR sm.checksum_attested_at IS NULL",
            "  ) THEN",
            "    RAISE EXCEPTION 'final schema_migrations attestation is incomplete';",
            "  END IF;",
            "END",
            "$omega_final$;",
            "COMMIT;",
            "",
        ]
    )
    return "\n".join(parts)


def _add_contract_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--old-ref", required=True)
    parser.add_argument("--candidate-ref", required=True)
    parser.add_argument("--release-version", required=True)
    parser.add_argument("--baseline-manifest", required=True)
    parser.add_argument("--baseline-manifest-sha256", required=True)
    parser.add_argument("--release-manifest", required=True)
    parser.add_argument("--release-manifest-sha256", required=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    verify = commands.add_parser("verify-manifests")
    _add_contract_arguments(verify)

    preflight = commands.add_parser("preflight")
    _add_contract_arguments(preflight)
    preflight.add_argument("--operational-ledger", required=True)
    preflight.add_argument("--gold-ledger", required=True)
    preflight.add_argument("--plan", required=True)
    preflight.add_argument("--allow-bootstrap-release-ledger", action="store_true")

    postflight = commands.add_parser("postflight")
    _add_contract_arguments(postflight)
    postflight.add_argument("--operational-ledger", required=True)
    postflight.add_argument("--gold-ledger", required=True)

    render = commands.add_parser("render-sql")
    render.add_argument("--plan", required=True)
    render.add_argument("--database", required=True, choices=("operational", "gold"))

    args = parser.parse_args()
    if args.command == "render-sql":
        plan = _load_json(Path(args.plan))
        contract = _validate_contract(_args_from_plan(plan))
        state = plan.get(f"{args.database}_state")
        sys.stdout.write(_render_sql(contract, args.database, state))
        return 0

    contract = _validate_contract(args)
    if args.command == "verify-manifests":
        print(
            "migration manifests verified: "
            "baseline=198+12 release=200+12 pending=99zzt,99zzu"
        )
        return 0

    operational = _load_ledger(Path(args.operational_ledger), "operational")
    gold = _load_ledger(Path(args.gold_ledger), "gold")
    require_release = args.command == "postflight"
    operational_state = _validate_ledger(
        contract,
        "operational",
        operational,
        require_release=require_release,
        allow_bootstrap_release=(
            args.command == "preflight" and args.allow_bootstrap_release_ledger
        ),
    )
    gold_state = _validate_ledger(
        contract, "gold", gold, require_release=require_release
    )
    if args.command == "preflight":
        _write_plan(contract, operational_state, gold_state, Path(args.plan))
        print(
            "two-database preflight verified: "
            f"operational={operational_state} gold={gold_state}"
        )
    else:
        print("migration postflight verified: operational=200 gold=12 null_checksums=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
