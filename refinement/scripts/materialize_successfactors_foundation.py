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


SUCCESSFACTORS_GOLD_FOUNDATION_ORDER = [
    "sap_successfactors_employee_360",
    "sap_successfactors_org_structure",
    "sap_successfactors_headcount_by_location",
    "sap_successfactors_headcount_by_department",
    "sap_successfactors_headcount_by_company",
    "sap_successfactors_manager_hierarchy",
]

ALLOWED_FOUNDATION_DATASETS = set(SUCCESSFACTORS_GOLD_FOUNDATION_ORDER)


class MaterializationContractError(RuntimeError):
    """Raised when a requested dataset is unsafe for this scoped runner."""


def _repo_root() -> Path:
    # In the service image this file lives under /app/scripts. In the checkout it
    # lives under refinement/scripts. Both layouts keep the service root one level
    # above this file.
    return Path(__file__).resolve().parents[1]


def _run_id() -> str:
    return "SF_FOUNDATION_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _default_evidence_dir() -> Path:
    return Path(os.environ.get("OMEGA_EVIDENCE_DIR", "/tmp/omega-evidence")) / "successfactors-foundation" / _run_id()


def _split_datasets(value: str | None) -> list[str]:
    if not value:
        return list(SUCCESSFACTORS_GOLD_FOUNDATION_ORDER)
    return [item.strip() for item in value.split(",") if item.strip()]


def _scope_context(tenant_id: str, workspace_id: str) -> dict[str, object]:
    return {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "active_tenant_id": tenant_id,
        "active_workspace_id": workspace_id,
        "role": "super_admin",
        "_server_trusted_context": True,
    }


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
            result = engine.materialize(ds, context)
            row_count = int(result.get("row_count") or 0)
            store.update_refresh(name, row_count)
            item = {
                "name": name,
                "status": "PASS",
                "dry_run": False,
                "row_count": row_count,
                "storage_uri": result.get("storage_uri"),
            }
        results.append(item)

    return {
        "status": "PASS",
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "datasets": results,
    }


def _load_live_dependencies():
    service_root = _repo_root()
    sys.path.insert(0, str(service_root))
    from app.dataset_store import DatasetStore
    from app.duckdb_engine import DuckDBEngine

    return DatasetStore(), DuckDBEngine()


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
        store, engine = _load_live_dependencies()
        summary = materialize_foundation(
            store=store,
            engine=engine,
            tenant_id=args.tenant_id,
            workspace_id=args.workspace_id,
            datasets=_split_datasets(args.datasets),
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
