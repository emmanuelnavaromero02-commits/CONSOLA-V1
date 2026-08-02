"""Real PostgreSQL proof for exact SuccessFactors active-headcount isolation."""

from __future__ import annotations

from pathlib import Path

import psycopg2
import pytest

from app.services.intelligence.successfactors_active_headcount import (
    ACTIVE_HEADCOUNT_DATASET,
)

TENANT_A = "11111111-1111-4111-8111-111111111111"
TENANT_B = "22222222-2222-4222-8222-222222222222"
WORKSPACE_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
WORKSPACE_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
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


def _staged_schema_sql() -> str:
    root = Path(__file__).parents[1] / "infra/init_gold"
    source = (root / "40_staged_publication_schema.sql").read_text(encoding="utf-8")
    fragment = (root / "fragments/40_verification_schema.sql").read_text(
        encoding="utf-8"
    )
    return source.replace(r"\ir fragments/40_verification_schema.sql", fragment)


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
                SELECT 'a-' || value, %s, %s,
                       CASE WHEN value <= 90 THEN 'Comercio' ELSE NULL END,
                       TRUE
                  FROM generate_series(1, 100) AS value
                """,
                (TENANT_A, WORKSPACE_A),
            )
            cur.execute(
                """
                INSERT INTO public.gold_sap_successfactors_employee_360
                    (user_id, tenant_id, workspace_id, company_name, is_active)
                VALUES
                    ('b-1', %s, %s, 'Servicios', TRUE),
                    ('b-2', %s, %s, 'Servicios', TRUE),
                    ('b-3', %s, %s, 'Servicios', TRUE),
                    ('inactive-a', %s, %s, 'Comercio', FALSE)
                """,
                (TENANT_B, WORKSPACE_B) * 3 + (TENANT_A, WORKSPACE_A),
            )
            cur.execute(
                "GRANT SELECT ON public.gold_sap_successfactors_employee_360 "
                "TO omega_refinement_gold"
            )
            cur.execute(
                "SELECT public.omega_apply_gold_rls_for_table"
                "('gold_sap_successfactors_employee_360')"
            )
            cur.execute(_staged_schema_sql())
        conn.commit()
    finally:
        conn.close()

    role_dsn = postgres_gold_with_native_rls.replace(
        f"postgres:{POSTGRES_PASSWORD}",
        f"omega_refinement_gold:{GOLD_ROLE_PASSWORD}",
    )
    monkeypatch.setenv("GOLD_DATABASE_URL", role_dsn)

    tenant_a = await query_successfactors_headcount_summaries(
        _user(TENANT_A, WORKSPACE_A)
    )
    tenant_b = await query_successfactors_headcount_summaries(
        _user(TENANT_B, WORKSPACE_B)
    )
    cross_scope = await query_successfactors_headcount_summaries(
        _user(TENANT_A, WORKSPACE_B)
    )

    assert tenant_a[ACTIVE_HEADCOUNT_DATASET]["total"] == 100
    assert tenant_b[ACTIVE_HEADCOUNT_DATASET]["total"] == 3
    assert cross_scope[ACTIVE_HEADCOUNT_DATASET] == {
        "rows": [],
        "total": None,
        "status": "unavailable",
        "error": "404: dataset unavailable: sap_successfactors_employee_360",
    }
