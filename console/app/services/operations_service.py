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


def _running_in_container() -> bool:
    return Path("/.dockerenv").exists() or bool(os.environ.get("KUBERNETES_SERVICE_HOST"))


def _service_url(env_name: str, docker_default: str, local_default: str) -> str:
    raw = os.environ.get(env_name)
    if raw:
        return raw.rstrip("/")
    return docker_default.rstrip("/") if _running_in_container() else local_default.rstrip("/")


_BASE_SERVICE_PROBES = {
    "console":    ("CONSOLE_INTERNAL_URL", "http://console:8000", "http://127.0.0.1:8000", "/api/system/info"),
    "workspace":  ("WORKSPACE_INTERNAL_URL", "http://workspace:8001", "http://127.0.0.1:8001", "/healthz"),
    "refinement": ("REFINEMENT_URL", "http://refinement:8500", "http://127.0.0.1:8500", "/healthz"),
    "mcp-infra":  ("MCP_INFRA_URL", "http://mcp-infra:8010", "http://127.0.0.1:8010", "/healthz"),
    "vault":      ("VAULT_URL", "http://vault:8300", "http://127.0.0.1:8300", "/healthz"),
    "airflow":    ("AIRFLOW_URL", "http://airflow:8080", "http://127.0.0.1:8082", "/health"),
    "superset":   ("SUPERSET_URL", "http://superset:8088", "http://127.0.0.1:8088", "/health"),
    "mailhog":    ("MAILHOG_URL", "http://mailhog:8025", "http://127.0.0.1:8025", "/api/v1/messages"),
    "minio":      ("MINIO_HEALTH_URL", "http://minio:9000", "http://127.0.0.1:9000", "/minio/health/live"),
}

_CARTRIDGE_SERVICE_PROBES = {
    "replicon":           ("replicon", "REPLICON_URL", "http://replicon:8201", "http://127.0.0.1:8201", "/health"),
    "hubspot":            ("hubspot", "HUBSPOT_URL", "http://hubspot:8210", "http://127.0.0.1:8210", "/health"),
    "salesforce":         ("salesforce", "SALESFORCE_URL", "http://salesforce:8205", "http://127.0.0.1:8205", "/health"),
    "sap-hcm":            ("sap_hcm", "SAP_HCM_URL", "http://sap-hcm:8202", "http://127.0.0.1:8202", "/health"),
    "sap-successfactors": ("sap_successfactors", "SAP_SUCCESSFACTORS_URL", "http://sap-successfactors:8203", "http://127.0.0.1:8203", "/health"),
    "sap-s4hana":         ("sap_s4hana", "SAP_S4HANA_URL", "http://sap-s4hana:8204", "http://127.0.0.1:8204", "/health"),
}


def _probe_url(env_name: str, docker_default: str, local_default: str, path: str) -> str:
    return f"{_service_url(env_name, docker_default, local_default)}{path}"


def service_probes(active_cartridges: set[str] | None = None) -> dict[str, str]:
    probes = {
        name: _probe_url(env_name, docker_default, local_default, path)
        for name, (env_name, docker_default, local_default, path) in _BASE_SERVICE_PROBES.items()
    }
    active = None if active_cartridges is None else {
        str(item).strip() for item in active_cartridges if str(item).strip()
    }
    cartridge_items = _CARTRIDGE_SERVICE_PROBES.items()
    if active is not None:
        cartridge_items = [
            (name, cfg)
            for name, cfg in cartridge_items
            if cfg[0] in active
        ]
    for name, (_cartridge, env_name, docker_default, local_default, path) in cartridge_items:
        probes[name] = _probe_url(env_name, docker_default, local_default, path)
    return probes


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


async def probe_services(active_cartridges: set[str] | None = None) -> list[dict]:
    tasks = [_probe_one(name, url) for name, url in service_probes(active_cartridges).items()]
    return await asyncio.gather(*tasks)
