"""Fase 1 (misión cliente): aserción de RLS al arranque en /readyz.

El runner de migraciones aplica FORCE RLS, pero nada lo asertaba en runtime:
un `docker compose up` sobre un volumen viejo sin migrar quedaba sin red. Este
check consulta pg_class (solo lectura) por tablas con workspace_id sin FORCE
RLS. Informativo por defecto (no voltea despliegues sanos); fail-closed 503
solo con OMEGA_REQUIRE_RLS_READY.
"""
from __future__ import annotations

import asyncio

from app.domains.system import readyz


class _Acq:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _Conn:
    def __init__(self, offenders):
        self._offenders = offenders

    async def fetchval(self, *a):
        return 1

    async def fetch(self, sql):
        assert "relforcerowsecurity" in sql and "workspace_id" in sql
        return [{"relname": n} for n in self._offenders]


class _Pool:
    def __init__(self, offenders):
        self._conn = _Conn(offenders)

    def acquire(self):
        return _Acq(self._conn)


def test_rls_check_up_when_all_forced():
    out = asyncio.run(readyz._tenant_rls_check(_Pool([])))
    assert out == {"status": "up"}


def test_rls_check_reports_offenders():
    out = asyncio.run(readyz._tenant_rls_check(_Pool(["entity_config", "foo"])))
    assert out["status"] == "down"
    assert out["count"] == 2
    assert "entity_config" in out["tables_without_force_rls"]


def test_rls_check_unknown_on_db_error():
    class _Broken:
        def acquire(self):
            raise RuntimeError("db down")

    out = asyncio.run(readyz._tenant_rls_check(_Broken()))
    assert out["status"] == "unknown"


def _build(environ, offenders):
    async def get_db_pool():
        return _Pool(offenders)

    async def dep(name, url, server):
        return {"status": "up"}

    async def cr_data(**k):
        return {"status": "up"}

    async def intel(**k):
        return {"status": "up", "required": False}

    return asyncio.run(readyz.build_readyz_checks(
        app=object(),
        query_params={},
        authenticated_user=None,
        environ=environ,
        refinement_url="http://r",
        mcp_infra_url="http://m",
        vault_url=lambda: "http://v",
        startup_readiness_status=lambda app: {"status": "up"},
        get_db_pool=get_db_pool,
        dependency_health=dep,
        control_room_data_check=cr_data,
        intelligence_readiness=intel,
        is_production_env=lambda: False,
        warn=lambda *a, **k: None,
    ))


def test_readyz_informative_by_default_does_not_break():
    checks, ok = _build({}, ["entity_config"])
    assert checks["rls"]["status"] == "down"
    assert checks["rls"]["required"] is False
    assert ok is True, "por defecto RLS es diagnóstico, no tumba readiness"


def test_readyz_fail_closed_when_required():
    checks, ok = _build({"OMEGA_REQUIRE_RLS_READY": "1"}, ["entity_config"])
    assert checks["rls"]["required"] is True
    assert ok is False, "con OMEGA_REQUIRE_RLS_READY, RLS faltante -> not ready"


def test_readyz_green_when_rls_ok_and_required():
    checks, ok = _build({"OMEGA_REQUIRE_RLS_READY": "1"}, [])
    assert checks["rls"]["status"] == "up"
    assert ok is True
