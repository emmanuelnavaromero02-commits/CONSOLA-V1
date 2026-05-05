"""
Dataset Store — persiste definiciones de datasets en PostgreSQL.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras


def _dsn() -> str:
    return (
        os.environ.get("DATABASE_URL", "")
        .replace("postgresql+psycopg2://", "postgresql://")
    )


def _conn():
    return psycopg2.connect(_dsn(), cursor_factory=psycopg2.extras.RealDictCursor)


class DatasetStore:
    def __init__(self, datasets_dir=None):
        # datasets_dir kept for API compatibility but ignored
        pass

    def list_datasets(self) -> list[dict]:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT name, description, layer, cartridge,
                       sources, schedule, last_refresh, row_count
                FROM datasets
                ORDER BY layer, name
            """)
            rows = cur.fetchall()
        return [
            {
                "name":         r["name"],
                "description":  r["description"] or "",
                "layer":        r["layer"],
                "cartridge":    r["cartridge"] or "",
                "sources":      r["sources"] or [],
                "schedule":     r["schedule"],
                "last_refresh": r["last_refresh"].isoformat() if r["last_refresh"] else None,
                "row_count":    r["row_count"],
            }
            for r in rows
        ]

    def get_dataset(self, name: str) -> dict | None:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT name, layer, cartridge, sources, sql_def,
                       column_mapping, schedule, description
                FROM datasets WHERE name = %s
            """, (name,))
            r = cur.fetchone()
        if not r:
            return None
        return {
            "name":           r["name"],
            "layer":          r["layer"],
            "cartridge":      r["cartridge"] or "unknown",
            "sources":        r["sources"] or [],
            "sql_def":        r["sql_def"] or "",
            "column_mapping": r["column_mapping"] or {},
            "schedule":       r["schedule"],
            "description":    r["description"] or "",
        }

    def save_dataset(self, ds: dict):
        sources        = ds.get("sources") or []
        column_mapping = ds.get("column_mapping") or {}
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO datasets
                  (name, layer, cartridge, sources, sql_def, description,
                   column_mapping, schedule, created_by_id, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (name) DO UPDATE SET
                  layer          = EXCLUDED.layer,
                  cartridge      = EXCLUDED.cartridge,
                  sources        = EXCLUDED.sources,
                  sql_def        = EXCLUDED.sql_def,
                  description    = EXCLUDED.description,
                  column_mapping = EXCLUDED.column_mapping,
                  schedule       = EXCLUDED.schedule,
                  created_by_id  = COALESCE(EXCLUDED.created_by_id, datasets.created_by_id),
                  updated_at     = NOW()
            """, (
                ds["name"],
                ds.get("layer", "silver"),
                ds.get("cartridge", ""),
                json.dumps(sources),
                ds.get("sql", ds.get("sql_def", "")),
                ds.get("description", ""),
                json.dumps(column_mapping),
                ds.get("schedule"),
                ds.get("created_by_id"),
            ))
            conn.commit()

    def delete_dataset(self, name: str) -> dict:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT layer, cartridge FROM datasets WHERE name = %s", (name,))
            row = cur.fetchone()
            if not row:
                return {"deleted": False, "error": "not found"}
            layer     = row["layer"]
            cartridge = row["cartridge"] or "unknown"
            cur.execute("DELETE FROM datasets WHERE name = %s", (name,))
            conn.commit()
        return {"deleted": True, "name": name, "layer": layer, "cartridge": cartridge}

    def update_refresh(self, name: str, row_count: int):
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("""
                UPDATE datasets
                SET last_refresh = NOW(), row_count = %s, updated_at = NOW()
                WHERE name = %s
            """, (row_count, name))
            conn.commit()
