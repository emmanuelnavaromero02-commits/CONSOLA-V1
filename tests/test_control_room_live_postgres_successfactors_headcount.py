"""Real PostgreSQL proof for exact SuccessFactors active-headcount isolation."""

from __future__ import annotations

import psycopg2
import pytest

from app.services.intelligence.successfactors_active_headcount import (
    ACTIVE_HEADCOUNT_DATASET,
)
from app.services.intelligence.successfactors_gold_headcount import (
    query_successfactors_headcount_summaries,
)
from tests.test_gold_native_rls_contract import (
    GOLD_ROLE_PASSWORD,
    POSTGRES_PASSWORD,
    postgres_gold_with_native_rls,
)


def _user(tenant_id: str, workspace_id: str) -> dict[str, str]:
    return {
        "tenant_id": tenant_id,
        "active_tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "active_workspace_id": workspace_id,
    }


@pytest.mark.asyncio
async def test_real_gold_postgres_exact_headcount_isolates_tenant_and_workspace(
    postgres_gold_with_native_rls: str,
    monkeypatch,
):
    conn = psycopg2.connect(postgres_gold_with_native_rls)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE public.gold_sap_successfactors_employee_360 (
                    user_id text NOT NULL,
                    tenant_id text NOT NULL,
                    workspace_id text NOT NULL,
                    company_name text,
                    is_active boolean NOT NULL
                )
                """
            )
            cur.execute(
                """
                INSERT INTO public.gold_sap_successfactors_employee_360
                    (user_id, tenant_id, workspace_id, company_name, is_active)
                SELECT 'a-' || value,
                       'tenant-a',
                       'workspace-a',
                       CASE WHEN value <= 90 THEN 'Comercio' ELSE NULL END,
                       TRUE
                  FROM generate_series(1, 100) AS value
                """
            )
            cur.execute(
                """
                INSERT INTO public.gold_sap_successfactors_employee_360
                    (user_id, tenant_id, workspace_id, company_name, is_active)
                VALUES
                    ('b-1', 'tenant-b', 'workspace-b', 'Servicios', TRUE),
                    ('b-2', 'tenant-b', 'workspace-b', 'Servicios', TRUE),
                    ('b-3', 'tenant-b', 'workspace-b', 'Servicios', TRUE),
                    ('inactive-a', 'tenant-a', 'workspace-a', 'Comercio', FALSE)
                """
            )
            cur.execute(
                "GRANT SELECT ON public.gold_sap_successfactors_employee_360 "
                "TO omega_refinement_gold"
            )
            cur.execute(
                "SELECT public.omega_apply_gold_rls_for_table"
                "('gold_sap_successfactors_employee_360')"
            )
        conn.commit()
    finally:
        conn.close()

    role_dsn = postgres_gold_with_native_rls.replace(
        f"postgres:{POSTGRES_PASSWORD}",
        f"omega_refinement_gold:{GOLD_ROLE_PASSWORD}",
    )
    monkeypatch.setenv("GOLD_DATABASE_URL", role_dsn)

    tenant_a = await query_successfactors_headcount_summaries(
        _user("tenant-a", "workspace-a")
    )
    tenant_b = await query_successfactors_headcount_summaries(
        _user("tenant-b", "workspace-b")
    )
    cross_scope = await query_successfactors_headcount_summaries(
        _user("tenant-a", "workspace-b")
    )

    assert tenant_a[ACTIVE_HEADCOUNT_DATASET]["total"] == 100
    assert tenant_b[ACTIVE_HEADCOUNT_DATASET]["total"] == 3
    assert cross_scope[ACTIVE_HEADCOUNT_DATASET] == {
        "rows": [],
        "total": 0,
        "status": "ready",
        "error": None,
    }
