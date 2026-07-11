#!/usr/bin/env python3
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


DATASET_ORDER = [
    ("inegi_indicator_metadata_latest", "silver"),
    ("inegi_indicator_observations_normalized", "silver"),
    ("inegi_indicator_quality", "silver"),
    ("inegi_market_context", "gold"),
]


class INEGIMaterializationError(RuntimeError):
    pass


def _dsn() -> str:
    return os.environ.get("DATABASE_URL", "").replace("postgresql+psycopg2://", "postgresql://")


def _run_id() -> str:
    return "INEGI_CONTEXT_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _default_evidence_dir() -> Path:
    return Path(os.environ.get("OMEGA_EVIDENCE_DIR", "/tmp/omega-evidence")) / "inegi-context" / _run_id()


def _scope_context(tenant_id: str, workspace_id: str) -> dict[str, object]:
    return {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "active_tenant_id": tenant_id,
        "active_workspace_id": workspace_id,
        "role": "admin",
        "permissions": ["datasets.read", "datasets.write"],
        "allowed_cartridges": ["inegi"],
        "_server_trusted_context": True,
    }


class ScopedDatasetStore:
    def __init__(self, tenant_id: str, workspace_id: str):
        self.tenant_id = tenant_id
        self.workspace_id = workspace_id

    def _conn(self):
        return psycopg2.connect(_dsn(), cursor_factory=psycopg2.extras.RealDictCursor)

    def _set_scope(self, cur) -> None:
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (self.tenant_id,))
        cur.execute("SELECT set_config('app.workspace_id', %s, true)", (self.workspace_id,))

    def get_dataset(self, name: str) -> dict | None:
        with self._conn() as conn, conn.cursor() as cur:
            self._set_scope(cur)
            cur.execute(
                """
                SELECT name, layer, cartridge, sources, sql_def, description,
                       column_mapping, schedule, row_count, workspace_id
                  FROM datasets
                 WHERE name = %s
                """,
                (name,),
            )
            row = cur.fetchone()
        return dict(row) if row else None

    def update_refresh(self, name: str, row_count: int) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            self._set_scope(cur)
            cur.execute(
                "UPDATE datasets SET last_refresh = NOW(), row_count = %s, updated_at = NOW() WHERE name = %s",
                (row_count, name),
            )
            conn.commit()


def materialize_inegi_context(
    *,
    store,
    engine,
    tenant_id: str,
    workspace_id: str,
    datasets: Iterable[tuple[str, str]] = DATASET_ORDER,
    dry_run: bool = False,
) -> dict[str, object]:
    if not tenant_id or not workspace_id:
        raise INEGIMaterializationError("tenant_id and workspace_id are required")
    context = _scope_context(tenant_id, workspace_id)
    results = []
    for name, expected_layer in datasets:
        ds = store.get_dataset(name)
        _validate_dataset(ds, name, expected_layer)
        if dry_run:
            item = {"name": name, "layer": expected_layer, "status": "PASS", "dry_run": True}
        else:
            result = engine.materialize(ds, context)
            row_count = int(result.get("row_count") or 0)
            store.update_refresh(name, row_count)
            item = {
                "name": name,
                "layer": expected_layer,
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


def _validate_dataset(ds: dict | None, name: str, expected_layer: str) -> None:
    if not ds:
        raise INEGIMaterializationError(f"{name} is not registered in datasets")
    if ds.get("cartridge") != "inegi":
        raise INEGIMaterializationError(f"{name} must belong to inegi")
    if ds.get("layer") != expected_layer:
        raise INEGIMaterializationError(f"{name} must be layer={expected_layer}")
    if not str(ds.get("sql_def") or "").strip():
        raise INEGIMaterializationError(f"{name} has no sql_def")


def _write_evidence(evidence_dir: Path, summary: dict[str, object]) -> None:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# INEGI Context Materialization",
        "",
        f"- status: `{summary.get('status')}`",
        f"- tenant_id: `{summary.get('tenant_id', '')}`",
        f"- workspace_id: `{summary.get('workspace_id', '')}`",
    ]
    if summary.get("error"):
        lines.append(f"- error: `{summary['error']}`")
    for row in summary.get("datasets") or []:
        if isinstance(row, dict):
            lines.append(f"- `{row.get('name')}`: {row.get('status')} rows={row.get('row_count')}")
    (evidence_dir / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Materialize governed INEGI Silver/Gold context.")
    parser.add_argument("--tenant-id", default=os.environ.get("OMEGA_TENANT_ID") or os.environ.get("TENANT_ID"))
    parser.add_argument("--workspace-id", default=os.environ.get("OMEGA_WORKSPACE_ID") or os.environ.get("WORKSPACE_ID"))
    parser.add_argument("--evidence-dir", type=Path, default=_default_evidence_dir())
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        from app.duckdb_engine import DuckDBEngine

        summary = materialize_inegi_context(
            store=ScopedDatasetStore(args.tenant_id, args.workspace_id),
            engine=DuckDBEngine(),
            tenant_id=args.tenant_id,
            workspace_id=args.workspace_id,
            dry_run=args.dry_run,
        )
        summary["evidence_dir"] = str(args.evidence_dir)
        _write_evidence(args.evidence_dir, summary)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        summary = {"status": "FAILED", "error": str(exc), "evidence_dir": str(args.evidence_dir)}
        _write_evidence(args.evidence_dir, summary)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
