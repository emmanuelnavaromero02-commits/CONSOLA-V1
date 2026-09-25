#!/usr/bin/env python3
"""Materialize the SAP SuccessFactors Gold foundation for one scoped workspace.

This is an operational repair runner, not a broad backfill tool. It refuses to
run without an explicit tenant/workspace scope and only accepts the known
SuccessFactors foundation Gold datasets.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import psycopg2
import psycopg2.extras


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.successfactors_fallbacks import fallback_dataset_for_successfactors


def _load_gold_dataset_orders() -> dict:
    candidates = [
        Path("/registry/cartridges/sap_successfactors/app/config/gold_dataset_orders.json"),
        Path(__file__).resolve().parents[2]
        / "cartridges" / "sap_successfactors" / "app" / "config" / "gold_dataset_orders.json",
    ]
    for path in candidates:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            talent = list(data["talent_order"])
            contract = list(data["talent_contract_order"])
            operational = list(data["talent_operational_order"])
            if contract + operational != talent:
                raise ValueError(
                    f"gold_dataset_orders.json invalido en {path}: "
                    "talent_contract_order + talent_operational_order != talent_order"
                )
            return {
                "foundation": list(data["foundation_order"]),
                "talent": talent,
                "contract": contract,
                "operational": operational,
            }
    raise FileNotFoundError(
        "No se encontro gold_dataset_orders.json (buscado en: "
        + "; ".join(str(p) for p in candidates)
        + "). El cartucho sap_successfactors debe estar montado en /registry/cartridges."
    )


_GOLD_ORDERS = _load_gold_dataset_orders()
SUCCESSFACTORS_GOLD_FOUNDATION_ORDER = _GOLD_ORDERS["foundation"]
SUCCESSFACTORS_GOLD_TALENT_ORDER = _GOLD_ORDERS["talent"]
SUCCESSFACTORS_GOLD_TALENT_CONTRACT_ORDER = _GOLD_ORDERS["contract"]
SUCCESSFACTORS_GOLD_TALENT_OPERATIONAL_ORDER = _GOLD_ORDERS["operational"]

ALLOWED_FOUNDATION_DATASETS = set(SUCCESSFACTORS_GOLD_FOUNDATION_ORDER) | set(SUCCESSFACTORS_GOLD_TALENT_ORDER)


class MaterializationContractError(RuntimeError):
    pass


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _run_id() -> str:
    return "SF_FOUNDATION_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _default_evidence_dir() -> Path:
    return Path(os.environ.get("OMEGA_EVIDENCE_DIR", "/tmp/omega-evidence")) / "successfactors-foundation" / _run_id()


def _split_datasets(value: str | None) -> list[str]:
    if not value:
        return list(SUCCESSFACTORS_GOLD_FOUNDATION_ORDER)
    return [item.strip() for item in value.split(",") if item.strip()]


def _datasets_for_phase(phase: str, explicit: str | None) -> list[str]:
    if explicit:
        return _split_datasets(explicit)
    if phase == "foundation":
        return list(SUCCESSFACTORS_GOLD_FOUNDATION_ORDER)
    if phase == "talent_contract":
        return list(SUCCESSFACTORS_GOLD_TALENT_CONTRACT_ORDER)
    if phase == "talent_operational":
        return list(SUCCESSFACTORS_GOLD_TALENT_OPERATIONAL_ORDER)
    if phase == "all":
        return list(SUCCESSFACTORS_GOLD_FOUNDATION_ORDER) + list(SUCCESSFACTORS_GOLD_TALENT_ORDER)
    raise MaterializationContractError(f"Unknown SuccessFactors materialization phase: {phase}")


def _scope_context(tenant_id: str, workspace_id: str) -> dict[str, object]:
    return {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "active_tenant_id": tenant_id,
        "active_workspace_id": workspace_id,
        "role": "super_admin",
        "_server_trusted_context": True,
    }


def _scoped_dsn() -> str:
    return (
        os.environ.get("DATABASE_URL", "")
        .replace("postgresql+psycopg2://", "postgresql://")
    )


class ScopedDatasetStore:

    def __init__(self, tenant_id: str, workspace_id: str):
        self.tenant_id = tenant_id
        self.workspace_id = workspace_id

    def _conn(self):
        return psycopg2.connect(_scoped_dsn(), cursor_factory=psycopg2.extras.RealDictCursor)

    def _set_scope(self, cur) -> None:
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (self.tenant_id,))
        cur.execute("SELECT set_config('app.workspace_id', %s, true)", (self.workspace_id,))
        cur.execute("SELECT set_config('app.platform_admin', %s, true)", ("false",))

    def get_dataset(self, name: str) -> dict | None:
        with self._conn() as conn, conn.cursor() as cur:
            self._set_scope(cur)
            cur.execute(
                """
                SELECT name, layer, cartridge, sources, sql_def,
                       column_mapping, schedule, description, last_refresh, row_count,
                       workspace_id, created_by_id
                FROM datasets WHERE name = %s
                """,
                (name,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {
            "name": row["name"],
            "layer": row["layer"],
            "cartridge": row["cartridge"] or "unknown",
            "sources": row["sources"] or [],
            "sql_def": row["sql_def"] or "",
            "column_mapping": row["column_mapping"] or {},
            "schedule": row["schedule"],
            "description": row["description"] or "",
            "last_refresh": row["last_refresh"].isoformat() if row.get("last_refresh") else None,
            "row_count": row["row_count"],
            "workspace_id": str(row["workspace_id"]) if row.get("workspace_id") else None,
            "created_by_id": row["created_by_id"],
        }

    def update_refresh(self, name: str, row_count: int) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            self._set_scope(cur)
            cur.execute(
                """
                UPDATE datasets
                SET last_refresh = NOW(), row_count = %s, updated_at = NOW()
                WHERE name = %s
                """,
                (row_count, name),
            )
            conn.commit()


def _contract_checked_dataset(store, name: str) -> dict:
    if name not in ALLOWED_FOUNDATION_DATASETS:
        raise MaterializationContractError(f"{name} is not an allowed SuccessFactors foundation dataset")
    ds = store.get_dataset(name)
    if not ds:
        raise MaterializationContractError(f"{name} is not registered in datasets")
    if ds.get("layer") != "gold":
        raise MaterializationContractError(f"{name} must be layer=gold, got {ds.get('layer')!r}")
    if ds.get("cartridge") != "sap_successfactors":
        raise MaterializationContractError(
            f"{name} must belong to sap_successfactors, got {ds.get('cartridge')!r}"
        )
    if not str(ds.get("sql_def") or "").strip():
        raise MaterializationContractError(f"{name} has no sql_def")
    return ds


def _materialize_with_operational_fallback(engine, ds: dict, context: dict) -> tuple[dict, bool, str | None]:
    try:
        return engine.materialize(ds, context), False, None
    except Exception as exc:
        fallback = fallback_dataset_for_successfactors(ds, exc)
        if not fallback:
            raise
        result = engine.materialize(fallback, context)
        return result, True, str(exc)[:1000]


def materialize_foundation(
    *,
    store,
    engine,
    tenant_id: str,
    workspace_id: str,
    datasets: Iterable[str] | None = None,
    dry_run: bool = False,
) -> dict[str, object]:
    if not tenant_id or not workspace_id:
        raise MaterializationContractError("tenant_id and workspace_id are required")

    names = list(datasets or SUCCESSFACTORS_GOLD_FOUNDATION_ORDER)
    checked = [(name, _contract_checked_dataset(store, name)) for name in names]
    context = _scope_context(tenant_id, workspace_id)
    results: list[dict[str, object]] = []
    used_fallback = False

    for name, ds in checked:
        if dry_run:
            item = {
                "name": name,
                "status": "PASS",
                "dry_run": True,
                "row_count": ds.get("row_count"),
                "storage_uri": None,
            }
        else:
            result, fallback, original_error = _materialize_with_operational_fallback(engine, ds, context)
            used_fallback = used_fallback or fallback
            row_count = int(result.get("row_count") or 0)
            store.update_refresh(name, row_count)
            item = {
                "name": name,
                "status": "PARTIAL" if fallback else "PASS",
                "dry_run": False,
                "row_count": row_count,
                "storage_uri": result.get("storage_uri"),
            }
            if fallback:
                item["fallback"] = True
                item["reason"] = "missing_materialized_dependency"
                item["original_error"] = original_error
        results.append(item)

    return {
        "status": "PARTIAL" if used_fallback else "PASS",
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "datasets": results,
    }


def _load_live_dependencies():
    service_root = _repo_root()
    sys.path.insert(0, str(service_root))
    from app.staged_publication_engine import StagedPublicationEngine

    return StagedPublicationEngine()


def _write_evidence(evidence_dir: Path, summary: dict[str, object]) -> None:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# SuccessFactors Foundation Materialization",
        "",
        f"- status: `{summary.get('status')}`",
        f"- tenant_id: `{summary.get('tenant_id', '')}`",
        f"- workspace_id: `{summary.get('workspace_id', '')}`",
    ]
    if summary.get("unblock_command"):
        lines.append(f"- unblock_command: `{summary['unblock_command']}`")
    if summary.get("error"):
        lines.append(f"- error: `{summary['error']}`")
    rows = summary.get("datasets")
    if isinstance(rows, list):
        lines.extend(["", "## Datasets"])
        for row in rows:
            lines.append(
                f"- `{row.get('name')}`: {row.get('status')} "
                f"rows={row.get('row_count')} storage={row.get('storage_uri')}"
            )
    (evidence_dir / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _blocked_summary(evidence_dir: Path, reason: str) -> dict[str, object]:
    return {
        "status": "BLOCKED",
        "reason": reason,
        "tenant_id": "",
        "workspace_id": "",
        "unblock_command": (
            "OMEGA_TENANT_ID=<tenant_uuid> OMEGA_WORKSPACE_ID=<workspace_uuid> "
            f"{sys.executable} scripts/materialize_successfactors_foundation.py"
        ),
        "evidence_dir": str(evidence_dir),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", default=os.environ.get("OMEGA_TENANT_ID") or os.environ.get("TENANT_ID"))
    parser.add_argument(
        "--workspace-id",
        default=os.environ.get("OMEGA_WORKSPACE_ID") or os.environ.get("WORKSPACE_ID"),
    )
    parser.add_argument("--datasets", default=os.environ.get("OMEGA_SF_FOUNDATION_DATASETS"))
    parser.add_argument(
        "--phase",
        choices=["foundation", "talent_contract", "talent_operational", "all"],
        default=os.environ.get("OMEGA_SF_MATERIALIZATION_PHASE", "foundation"),
        help="Dataset phase to materialize when --datasets is omitted.",
    )
    parser.add_argument("--evidence-dir", type=Path, default=_default_evidence_dir())
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    evidence_dir: Path = args.evidence_dir
    if not args.tenant_id or not args.workspace_id:
        summary = _blocked_summary(evidence_dir, "OMEGA_TENANT_ID/OMEGA_WORKSPACE_ID missing")
        _write_evidence(evidence_dir, summary)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 2

    try:
        engine = _load_live_dependencies()
        store = ScopedDatasetStore(args.tenant_id, args.workspace_id)
        summary = materialize_foundation(
            store=store,
            engine=engine,
            tenant_id=args.tenant_id,
            workspace_id=args.workspace_id,
            datasets=_datasets_for_phase(args.phase, args.datasets),
            dry_run=args.dry_run,
        )
        summary["evidence_dir"] = str(evidence_dir)
        _write_evidence(evidence_dir, summary)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    except MaterializationContractError as exc:
        summary = {
            "status": "FAIL",
            "tenant_id": args.tenant_id,
            "workspace_id": args.workspace_id,
            "error": str(exc),
            "evidence_dir": str(evidence_dir),
        }
        _write_evidence(evidence_dir, summary)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 1
    except Exception as exc:
        summary = {
            "status": "FAIL",
            "tenant_id": args.tenant_id,
            "workspace_id": args.workspace_id,
            "error": f"{type(exc).__name__}: {exc}",
            "evidence_dir": str(evidence_dir),
        }
        _write_evidence(evidence_dir, summary)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
