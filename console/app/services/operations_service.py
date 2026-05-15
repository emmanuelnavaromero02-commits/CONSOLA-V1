from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx

from app.services import auth


async def list_migrations() -> list[dict]:
    pool = await auth.pool()
    rows = await pool.fetch(
        "SELECT filename, applied_at, checksum FROM schema_migrations ORDER BY filename"
    )
    return [dict(r) for r in rows]


async def get_system_version() -> str:
    here = Path(__file__).resolve()
    candidates = [
        Path("/app/VERSION"),
        here.parent.parent.parent.parent / "VERSION",  # repo root from services/
        here.parent.parent.parent / "VERSION",         # repo root from app/ (legacy)
    ]
    for p in candidates:
        try:
            if p.exists():
                return p.read_text().strip()
        except Exception:
            continue
    return "unknown"


SERVICE_PROBES = {
    "console":            "http://localhost:8000/api/system/info",
    "workspace":          "http://workspace:8001/healthz",
    "refinement":         "http://refinement:8500/healthz",
    "mcp-infra":          "http://mcp-infra:8010/healthz",
    "vault":              "http://vault:8300/healthz",
    "replicon":           "http://replicon:8201/health",
    "sap-hcm":            "http://sap-hcm:8202/health",
    "sap-successfactors": "http://sap-successfactors:8203/health",
    "sap-s4hana":         "http://sap-s4hana:8204/health",
    "airflow":            "http://airflow:8080/health",
    "superset":           "http://superset:8088/health",
    "mailhog":            "http://mailhog:8025/api/v1/messages",
    "minio":              "http://minio:9000/minio/health/live",
}


async def _probe_one(name: str, url: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(url)
            if name == "console":
                ok = r.status_code in (200, 401, 403)
            else:
                ok = r.status_code < 500
            return {"name": name, "status": "up" if ok else "down", "code": r.status_code}
    except Exception as e:
        return {"name": name, "status": "down", "error": type(e).__name__}


async def probe_services() -> list[dict]:
    tasks = [_probe_one(name, url) for name, url in SERVICE_PROBES.items()]
    return await asyncio.gather(*tasks)
