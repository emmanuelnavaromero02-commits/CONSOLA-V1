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


def _base_url(env_name: str, default: str) -> str:
    return os.environ.get(env_name, default).rstrip("/")


def service_probes() -> dict[str, str]:
    return {
        "console":            f"{_base_url('CONSOLE_INTERNAL_URL', 'http://console:8000')}/api/system/info",
        "workspace":          f"{_base_url('WORKSPACE_INTERNAL_URL', 'http://workspace:8001')}/healthz",
        "refinement":         f"{_base_url('REFINEMENT_URL', 'http://refinement:8500')}/healthz",
        "mcp-infra":          f"{_base_url('MCP_INFRA_URL', 'http://mcp-infra:8010')}/healthz",
        "vault":              f"{_base_url('VAULT_URL', 'http://vault:8300')}/healthz",
        "replicon":           f"{_base_url('REPLICON_URL', 'http://replicon:8201')}/health",
        "sap-hcm":            f"{_base_url('SAP_HCM_URL', 'http://sap-hcm:8202')}/health",
        "sap-successfactors": f"{_base_url('SAP_SUCCESSFACTORS_URL', 'http://sap-successfactors:8203')}/health",
        "sap-s4hana":         f"{_base_url('SAP_S4HANA_URL', 'http://sap-s4hana:8204')}/health",
        "airflow":            f"{_base_url('AIRFLOW_URL', 'http://airflow:8080')}/health",
        "superset":           f"{_base_url('SUPERSET_URL', 'http://superset:8088')}/health",
        "mailhog":            f"{_base_url('MAILHOG_URL', 'http://mailhog:8025')}/api/v1/messages",
        "minio":              f"{_base_url('MINIO_HEALTH_URL', 'http://minio:9000')}/minio/health/live",
    }


SERVICE_PROBES = service_probes()


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
    tasks = [_probe_one(name, url) for name, url in service_probes().items()]
    return await asyncio.gather(*tasks)
