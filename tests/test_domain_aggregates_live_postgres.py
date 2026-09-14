"""Mission 1 — real PostgreSQL proof for the domain aggregates.

Mirrors tests/test_control_room_live_postgres_talent_population.py: seeds Gold
tables beyond the 5,000-row preview cap into a Docker Postgres with native RLS
+ the staged publication ledger, then asserts the aggregates count the whole
population, honour tenant/workspace isolation, and degrade cleanly when a
dataset is missing. Skips when Docker is not available.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import psycopg2
import pytest

from app.services.intelligence.finance_aggregates import query_billable_hours_logged
from app.services.intelligence.risk_aggregates import (
    query_attrition_risk_population,
    query_deal_slippage,
)
from tests.test_gold_native_rls_contract import (
    GOLD_ROLE_PASSWORD,
    POSTGRES_PASSWORD,
    postgres_gold_with_native_rls,  # noqa: F401 — pytest fixture
)

TENANT_A = "11111111-1111-4111-8111-111111111111"
TENANT_B = "22222222-2222-4222-8222-222222222222"
WORKSPACE_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
WORKSPACE_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

AS_OF = date(2026, 9, 13)
POPULATION = 6000  # beyond the 5,000-row preview cap
EXTRA = 10


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


def _seed(dsn: str) -> None:
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            # ── salesforce_deals_en_riesgo ──────────────────────────────
            cur.execute(
                """
                CREATE TABLE public.gold_salesforce_deals_en_riesgo (
                    tenant_id text NOT NULL,
                    workspace_id text NOT NULL,
                    opportunity_id text,
                    opportunity_name text,
                    vendedor text,
                    stage_name text,
                    amount numeric,
                    close_date date,
                    last_activity date,
                    dias_sin_actividad integer,
                    motivo_riesgo text
                )
                """
            )
            cur.execute(
                """
                INSERT INTO public.gold_salesforce_deals_en_riesgo
                SELECT %s, %s, 'opp-' || g, 'Deal ' || g, 'seller', 'Negotiation', 100,
                       %s::date - 10, %s::date - 20, 20, 'cierre vencido'
                  FROM generate_series(1, %s) g
                """,
                (TENANT_A, WORKSPACE_A, AS_OF, AS_OF, POPULATION),
            )
            cur.execute(
                """
                INSERT INTO public.gold_salesforce_deals_en_riesgo
                SELECT %s, %s, 'late-' || g, 'Late ' || g, 'seller', 'Proposal', 1000,
                       %s::date - 45, NULL, NULL, 'cierre vencido'
                  FROM generate_series(1, %s) g
                """,
                (TENANT_A, WORKSPACE_A, AS_OF, EXTRA),
            )
            cur.execute(
                """
                INSERT INTO public.gold_salesforce_deals_en_riesgo
                SELECT %s, %s, 'future-' || g, 'Future ' || g, 'seller', 'Proposal', 5000,
                       %s::date + 30, NULL, NULL, 'sin actividad reciente'
                  FROM generate_series(1, 5) g
                """,
                (TENANT_A, WORKSPACE_A, AS_OF),
            )
            cur.execute(
                """
                INSERT INTO public.gold_salesforce_deals_en_riesgo
                SELECT %s, %s, 'b-' || g, 'B ' || g, 'seller', 'Negotiation', 7,
                       %s::date - 3, NULL, NULL, 'cierre vencido'
                  FROM generate_series(1, 3) g
                """,
                (TENANT_B, WORKSPACE_B, AS_OF),
            )
            # ── sap_successfactors_talent_retention_risk (+ action candidates) ──
            cur.execute(
                """
                CREATE TABLE public.gold_sap_successfactors_talent_retention_risk (
                    tenant_id text NOT NULL,
                    workspace_id text NOT NULL,
                    user_id text,
                    department_name text,
                    risk_band text,
                    retention_risk_score numeric,
                    invalid_score_input boolean,
                    status text
                )
                """
            )
            cur.execute(
                """
                INSERT INTO public.gold_sap_successfactors_talent_retention_risk
                SELECT %s, %s, 'u' || g, 'Operaciones', 'high', 80, FALSE, 'recommendation_only'
                  FROM generate_series(1, %s) g
                """,
                (TENANT_A, WORKSPACE_A, POPULATION),
            )
            cur.execute(
                """
                INSERT INTO public.gold_sap_successfactors_talent_retention_risk
                SELECT %s, %s, 'm' || g, 'Ventas', 'medium', 50, FALSE, 'recommendation_only'
                  FROM generate_series(1, %s) g
                """,
                (TENANT_A, WORKSPACE_A, EXTRA),
            )
            cur.execute(
                """
                INSERT INTO public.gold_sap_successfactors_talent_retention_risk
                SELECT %s, %s, 'x' || g, 'Ventas', 'insufficient_data', NULL, TRUE, 'blocked'
                  FROM generate_series(1, 4) g
                """,
                (TENANT_A, WORKSPACE_A),
            )
            cur.execute(
                """
                INSERT INTO public.gold_sap_successfactors_talent_retention_risk
                SELECT %s, %s, 'b' || g, 'Otro', 'high', 90, FALSE, 'recommendation_only'
                  FROM generate_series(1, 2) g
                """,
                (TENANT_B, WORKSPACE_B),
            )
            cur.execute(
                """
                CREATE TABLE public.gold_sap_successfactors_talent_action_candidates (
                    tenant_id text NOT NULL,
                    workspace_id text NOT NULL,
                    action_id text,
                    severity text,
                    affected_count integer
                )
                """
            )
            cur.execute(
                """
                INSERT INTO public.gold_sap_successfactors_talent_action_candidates
                VALUES (%s, %s, 'talent_retention_risk', 'high', %s),
                       (%s, %s, 'talent_nine_box', 'low', 12)
                """,
                (TENANT_A, WORKSPACE_A, POPULATION, TENANT_A, WORKSPACE_A),
            )
            # ── consultor_mensual (replicon) ─────────────────────────────
            cur.execute(
                """
                CREATE TABLE public.gold_consultor_mensual (
                    tenant_id text NOT NULL,
                    workspace_id text NOT NULL,
                    mes date,
                    proyecto text,
                    project_name text,
                    cliente text,
                    consultor text,
                    horas_facturables numeric,
                    billing_rate_usd numeric
                )
                """
            )
            cur.execute(
                """
                INSERT INTO public.gold_consultor_mensual
                SELECT %s, %s, DATE '2026-08-01', 'P-' || (g %% 3), 'Project ' || (g %% 3),
                       'ACME', 'c' || (g %% 7), 2, 50
                  FROM generate_series(1, %s) g
                """,
                (TENANT_A, WORKSPACE_A, POPULATION),
            )
            cur.execute(
                """
                INSERT INTO public.gold_consultor_mensual
                SELECT %s, %s, DATE '2026-01-01', 'OLD', 'Old', 'ACME', 'c1', 999, 1
                  FROM generate_series(1, 5)
                """,
                (TENANT_A, WORKSPACE_A),
            )
            cur.execute(
                """
                INSERT INTO public.gold_consultor_mensual
                VALUES (%s, %s, DATE '2026-09-01', 'B-1', 'B', 'B Corp', 'b1', 8, 10)
                """,
                (TENANT_B, WORKSPACE_B),
            )
            for table in (
                "gold_salesforce_deals_en_riesgo",
                "gold_sap_successfactors_talent_retention_risk",
                "gold_sap_successfactors_talent_action_candidates",
                "gold_consultor_mensual",
            ):
                cur.execute(f"GRANT SELECT ON public.{table} TO omega_refinement_gold")
                cur.execute(f"SELECT public.omega_apply_gold_rls_for_table('{table}')")
            cur.execute(_staged_schema_sql())
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(scope="module")
def gold_dsn(postgres_gold_with_native_rls: str) -> str:  # noqa: F811 — pytest fixture
    _seed(postgres_gold_with_native_rls)
    return postgres_gold_with_native_rls.replace(
        f"postgres:{POSTGRES_PASSWORD}",
        f"omega_refinement_gold:{GOLD_ROLE_PASSWORD}",
    )


@pytest.mark.asyncio
async def test_deal_slippage_counts_all_rows_beyond_the_preview_cap(
    gold_dsn, monkeypatch
):
    monkeypatch.setenv("GOLD_DATABASE_URL", gold_dsn)

    result = await query_deal_slippage(
        _user(TENANT_A, WORKSPACE_A), top_n=3, as_of=AS_OF
    )

    assert result.status == "ready", result.error
    assert (
        result.deals == POPULATION + EXTRA
    ), "must be the full COUNT(*), not the 5,000-row cap"
    assert result.amount_total == POPULATION * 100 + EXTRA * 1000
    assert result.buckets["1_30"]["deals"] == POPULATION
    assert result.buckets["31_60"]["deals"] == EXTRA
    assert result.buckets["over_60"]["deals"] == 0
    assert {row["stage_name"]: row["deals"] for row in result.by_stage} == {
        "Negotiation": POPULATION,
        "Proposal": EXTRA,
    }
    assert len(result.top_deals) == 3
    assert result.top_deals[0]["amount"] == 1000.0
    assert "vendedor" not in result.top_deals[0]
    assert (
        result.evidence_refs[0]["relation"]
        == '"public"."gold_salesforce_deals_en_riesgo"'
    )

    isolated = await query_deal_slippage(_user(TENANT_B, WORKSPACE_B), as_of=AS_OF)
    assert isolated.status == "ready", isolated.error
    assert isolated.deals == 3

    cross = await query_deal_slippage(_user(TENANT_A, WORKSPACE_B), as_of=AS_OF)
    assert cross.status == "unavailable"
    assert cross.deals is None


@pytest.mark.asyncio
async def test_attrition_risk_population_reuses_talent_counts(gold_dsn, monkeypatch):
    monkeypatch.setenv("GOLD_DATABASE_URL", gold_dsn)

    result = await query_attrition_risk_population(
        _user(TENANT_A, WORKSPACE_A), top_n=5
    )

    assert result.status == "ready", result.error
    assert result.total == POPULATION + EXTRA + 4
    assert result.high == POPULATION
    assert result.medium == EXTRA
    assert result.insufficient_data == 4
    assert result.avg_score_valid == pytest.approx(
        (80 * POPULATION + 50 * EXTRA) / (POPULATION + EXTRA)
    )
    assert result.departments_top[0] == {
        "department_name": "Operaciones",
        "total": POPULATION,
        "high": POPULATION,
        "medium": 0,
    }
    assert result.talent_action["affected_count"] == POPULATION
    assert result.talent_action["severity"] == "high"

    isolated = await query_attrition_risk_population(_user(TENANT_B, WORKSPACE_B))
    assert isolated.status == "ready", isolated.error
    assert isolated.high == 2
    # Tenant B has no action_candidates head: degrades to a note, never fails.
    assert isolated.talent_action is None


@pytest.mark.asyncio
async def test_billable_hours_logged_sums_the_window(gold_dsn, monkeypatch):
    monkeypatch.setenv("GOLD_DATABASE_URL", gold_dsn)

    result = await query_billable_hours_logged(
        _user(TENANT_A, WORKSPACE_A), months=2, top_n=2, as_of=AS_OF
    )

    assert result.status == "ready", result.error
    assert result.billable_hours == POPULATION * 2
    assert result.billable_amount_usd == POPULATION * 2 * 50
    assert result.projects_affected == 3
    assert result.contributors == 7
    assert len(result.top_projects) == 2
    assert result.top_projects[0]["billable_hours"] == 2000 * 2
    # The January rows (outside the window) must not leak into the totals.
    assert result.billable_hours != POPULATION * 2 + 5 * 999

    isolated = await query_billable_hours_logged(
        _user(TENANT_B, WORKSPACE_B), as_of=AS_OF
    )
    assert isolated.status == "ready", isolated.error
    assert isolated.billable_hours == 8


@pytest.mark.asyncio
async def test_missing_dataset_is_unavailable_not_an_exception(gold_dsn, monkeypatch):
    monkeypatch.setenv("GOLD_DATABASE_URL", gold_dsn)
    from app.services.intelligence.finance_aggregates import query_project_margin

    result = await query_project_margin(_user(TENANT_A, WORKSPACE_A), as_of=AS_OF)

    assert result.status == "unavailable"
    assert result.error == "missing: dataset unavailable: pnl_mensual"
    assert result.supported is True
