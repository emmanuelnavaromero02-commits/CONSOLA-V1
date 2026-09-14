"""Mission 1 — real PostgreSQL proof for all nine domain aggregates.

Mirrors tests/test_control_room_live_postgres_talent_population.py: seeds Gold
tables beyond the 5,000-row preview cap into a Docker Postgres with native RLS
+ the staged publication ledger (legacy heads backfilled by
40_staged_publication_schema.sql), then asserts every aggregate counts the
whole population, honours tenant/workspace isolation and degrades cleanly.
The console run-log tables used by Operations are created in the same
container and reached through ``auth.pool()`` (DATABASE_URL). Skips when
Docker is not available.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import psycopg2
import pytest
import pytest_asyncio

from app.services import auth
from app.services.intelligence.finance_aggregates import (
    query_billable_hours_logged,
    query_labor_cost_by_department,
    query_project_margin,
)
from app.services.intelligence.operations_aggregates import (
    query_absence_rate_company_by_type,
    query_data_freshness_by_cartridge,
    query_pipeline_health,
)
from app.services.intelligence.risk_aggregates import (
    query_attrition_risk_population,
    query_deal_slippage,
    query_employment_end_expiry,
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
NOW = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
POPULATION = 6000  # beyond the 5,000-row preview cap
EXTRA = 10

GOLD_TABLES = (
    "gold_salesforce_deals_en_riesgo",
    "gold_sap_successfactors_talent_retention_risk",
    "gold_sap_successfactors_talent_action_candidates",
    "gold_sap_successfactors_employee_360",
    "gold_consultor_mensual",
    "gold_costo_consultor_mensual",
    "gold_pnl_mensual",
    "gold_absence_by_type_and_month",
    "gold_headcount_by_department",
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


def _seed_gold(cur) -> None:
    # ── salesforce_deals_en_riesgo ──────────────────────────────────────
    cur.execute(
        """
        CREATE TABLE public.gold_salesforce_deals_en_riesgo (
            tenant_id text NOT NULL, workspace_id text NOT NULL,
            opportunity_id text, opportunity_name text, vendedor text,
            stage_name text, amount numeric, close_date date, last_activity date,
            dias_sin_actividad integer, motivo_riesgo text
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
            tenant_id text NOT NULL, workspace_id text NOT NULL,
            user_id text, department_name text, risk_band text,
            retention_risk_score numeric, invalid_score_input boolean, status text
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
            tenant_id text NOT NULL, workspace_id text NOT NULL,
            action_id text, severity text, affected_count integer
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
    # ── sap_successfactors_employee_360 ─────────────────────────────────
    cur.execute(
        """
        CREATE TABLE public.gold_sap_successfactors_employee_360 (
            tenant_id text NOT NULL, workspace_id text NOT NULL,
            user_id_hash text, full_name text, department_name text,
            company_name text, start_date date, end_date date, is_active boolean
        )
        """
    )
    cur.execute(
        """
        INSERT INTO public.gold_sap_successfactors_employee_360
        SELECT %s, %s, 'h' || g, 'Name ' || g, 'Ingenieria', 'ACME',
               DATE '2020-01-01', DATE '2030-12-31', TRUE
          FROM generate_series(1, %s) g
        """,
        (TENANT_A, WORKSPACE_A, POPULATION),
    )
    for count, days, department, active in (
        (4, 10, "Soporte", True),
        (5, 45, "Ventas", True),
        (6, 80, "Ventas", True),
        (3, 100, "Ventas", True),
        (2, 5, "Soporte", False),
    ):
        cur.execute(
            """
            INSERT INTO public.gold_sap_successfactors_employee_360
            SELECT %s, %s, 'e' || g, 'Name ' || g, %s, 'ACME',
                   DATE '2021-01-01', %s::date + %s, %s
              FROM generate_series(1, %s) g
            """,
            (TENANT_A, WORKSPACE_A, department, AS_OF, days, active, count),
        )
    cur.execute(
        """
        INSERT INTO public.gold_sap_successfactors_employee_360
        VALUES (%s, %s, 'hb', 'B', 'Otro', 'B Corp', DATE '2021-01-01', %s::date + 7, TRUE)
        """,
        (TENANT_B, WORKSPACE_B, AS_OF),
    )
    # ── consultor_mensual (replicon) ─────────────────────────────────────
    cur.execute(
        """
        CREATE TABLE public.gold_consultor_mensual (
            tenant_id text NOT NULL, workspace_id text NOT NULL,
            mes date, proyecto text, project_name text, cliente text,
            consultor text, horas_facturables numeric, billing_rate_usd numeric
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
    # ── costo_consultor_mensual (replicon) ───────────────────────────────
    cur.execute(
        """
        CREATE TABLE public.gold_costo_consultor_mensual (
            tenant_id text NOT NULL, workspace_id text NOT NULL,
            mes date, userid text, nombre_completo text, departamento text,
            rate_costo numeric, horas_ejecutadas numeric, costo_ejecutado numeric,
            costo_hundido numeric, costo_potencial_mes numeric
        )
        """
    )
    cur.execute(
        """
        INSERT INTO public.gold_costo_consultor_mensual
        SELECT %s, %s, DATE '2026-08-01', 'u' || (g %% 7), 'Name', 'SAP', 10, 1, 10, 2, 12
          FROM generate_series(1, %s) g
        """,
        (TENANT_A, WORKSPACE_A, POPULATION // 2),
    )
    cur.execute(
        """
        INSERT INTO public.gold_costo_consultor_mensual
        SELECT %s, %s, DATE '2026-08-01', 'd' || (g %% 5), 'Name', 'Data', 5, 1, 5, 1, 6
          FROM generate_series(1, %s) g
        """,
        (TENANT_A, WORKSPACE_A, POPULATION // 2),
    )
    cur.execute(
        """
        INSERT INTO public.gold_costo_consultor_mensual
        SELECT %s, %s, DATE '2026-09-01', 'u1', 'Name', 'SAP', 999, 1, 999, 0, 999
          FROM generate_series(1, 3)
        """,
        (TENANT_A, WORKSPACE_A),
    )
    # ── pnl_mensual (replicon) ───────────────────────────────────────────
    cur.execute(
        """
        CREATE TABLE public.gold_pnl_mensual (
            tenant_id text NOT NULL, workspace_id text NOT NULL,
            mes date, proyecto text, project_name text, cliente text,
            tipo_proyecto text, revenue_base_amount numeric,
            cost_direct_base_amount numeric, cost_sunk_base_amount numeric,
            original_billing_amount numeric, original_currency text,
            horas_facturables numeric, financial_status text,
            revenue_usd numeric, margen_bruto_usd numeric
        )
        """
    )
    cur.execute(
        """
        INSERT INTO public.gold_pnl_mensual VALUES
        (%s, %s, DATE '2026-08-01', 'P-1', 'Alpha', 'ACME', 'T&M', 100000, 60000, 5000,
         90000, 'USD', 900, 'missing_base_currency', NULL, NULL),
        (%s, %s, DATE '2026-09-01', 'P-9', 'Omega', 'ACME', 'FPP', 10000, 14000, 1000,
         0, 'MXN', 120, 'missing_base_currency', NULL, NULL),
        (%s, %s, DATE '2026-05-01', 'OLD', 'Old', 'ACME', 'T&M', 999999, 0, 0,
         0, 'USD', 1, 'missing_base_currency', NULL, NULL)
        """,
        (TENANT_A, WORKSPACE_A, TENANT_A, WORKSPACE_A, TENANT_A, WORKSPACE_A),
    )
    cur.execute(
        """
        INSERT INTO public.gold_pnl_mensual
        SELECT %s, %s, DATE '2026-08-01', 'P-2', 'Beta', 'Globex', 'T&M', 10, 4, 1,
               10, 'USD', 1, 'missing_base_currency', NULL, NULL
          FROM generate_series(1, %s)
        """,
        (TENANT_A, WORKSPACE_A, POPULATION),
    )
    # ── sap_hcm absence + headcount ──────────────────────────────────────
    cur.execute(
        """
        CREATE TABLE public.gold_absence_by_type_and_month (
            tenant_id text NOT NULL, workspace_id text NOT NULL,
            absence_month date, absence_type text, total_days_workable numeric,
            employees_affected bigint, absence_records bigint
        )
        """
    )
    cur.execute(
        """
        INSERT INTO public.gold_absence_by_type_and_month VALUES
        (%s, %s, DATE '2026-08-01', '0100', 150, 40, 55),
        (%s, %s, DATE '2026-08-01', '0200', 60, 12, 14),
        (%s, %s, DATE '2026-09-01', '0100', 999, 1, 1),
        (%s, %s, DATE '2026-08-01', '0100', 5, 1, 1)
        """,
        (
            TENANT_A,
            WORKSPACE_A,
            TENANT_A,
            WORKSPACE_A,
            TENANT_A,
            WORKSPACE_A,
            TENANT_B,
            WORKSPACE_B,
        ),
    )
    cur.execute(
        """
        CREATE TABLE public.gold_headcount_by_department (
            tenant_id text NOT NULL, workspace_id text NOT NULL,
            org_id text, org_name text, headcount bigint, snapshot_month date
        )
        """
    )
    cur.execute(
        """
        INSERT INTO public.gold_headcount_by_department VALUES
        (%s, %s, '10', 'Ops', 60, DATE '2026-09-01'),
        (%s, %s, '20', 'Sales', 40, DATE '2026-09-01'),
        (%s, %s, '30', 'B', 10, DATE '2026-09-01')
        """,
        (TENANT_A, WORKSPACE_A, TENANT_A, WORKSPACE_A, TENANT_B, WORKSPACE_B),
    )


def _seed_console(cur) -> None:
    """Console run-log tables (normally in DATABASE_URL) with the columns the
    Operations aggregates reference."""
    cur.execute(
        """
        CREATE TABLE public.pipeline_runs (
            run_id text PRIMARY KEY, dag_id text NOT NULL, cartridge_id text NOT NULL,
            entity text NOT NULL, status text NOT NULL, started_at timestamptz,
            finished_at timestamptz, error_message text,
            tenant_id uuid, workspace_id uuid
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE public.extraction_runs (
            run_id text PRIMARY KEY, cartridge_id text NOT NULL, entity_name text NOT NULL,
            run_type text, status text NOT NULL, started_at timestamptz,
            finished_at timestamptz, error_message text,
            tenant_id uuid, workspace_id uuid
        )
        """
    )
    rows = [
        # pipeline_runs: (run_id, dag, cartridge, entity, status, started, finished, error, tenant, ws)
        (
            "p1",
            "sf",
            "sap_successfactors",
            "EmpJob",
            "failed",
            NOW - timedelta(hours=3),
            NOW - timedelta(hours=2, minutes=50),
            "timeout",
            TENANT_A,
            WORKSPACE_A,
        ),
        (
            "p2",
            "sf",
            "sap_successfactors",
            "EmpJob",
            "failed",
            NOW - timedelta(hours=5),
            NOW - timedelta(hours=4),
            "timeout",
            TENANT_A,
            WORKSPACE_A,
        ),
        (
            "p3",
            "sf",
            "sap_successfactors",
            "PerPerson",
            "success",
            NOW - timedelta(hours=1),
            NOW - timedelta(minutes=50),
            None,
            TENANT_A,
            WORKSPACE_A,
        ),
        (
            "p4",
            "sf",
            "sap_successfactors",
            "PerPerson",
            "failed",
            NOW - timedelta(days=3),
            NOW - timedelta(days=3),
            "boom",
            TENANT_A,
            WORKSPACE_A,
        ),
        (
            "p5",
            "sf",
            "sap_successfactors",
            "PerPerson",
            "success",
            NOW - timedelta(days=6),
            NOW - timedelta(days=6),
            None,
            TENANT_A,
            WORKSPACE_A,
        ),
        (
            "p6",
            "sf",
            "sap_successfactors",
            "PerPerson",
            "failed",
            NOW - timedelta(days=10),
            NOW - timedelta(days=10),
            "old",
            TENANT_A,
            WORKSPACE_A,
        ),
        (
            "p7",
            "replicon",
            "replicon",
            "TimeEntry",
            "success",
            NOW - timedelta(hours=31),
            NOW - timedelta(hours=30),
            None,
            TENANT_A,
            WORKSPACE_A,
        ),
        (
            "p8",
            "sf",
            "sap_successfactors",
            "EmpJob",
            "failed",
            NOW - timedelta(hours=2),
            NOW - timedelta(hours=1),
            "other tenant",
            TENANT_B,
            WORKSPACE_B,
        ),
    ]
    cur.executemany(
        "INSERT INTO public.pipeline_runs VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows
    )
    extraction = [
        (
            "e1",
            "sap_hcm",
            "PA0001",
            "incremental",
            "failed",
            NOW - timedelta(hours=2),
            NOW - timedelta(hours=1, minutes=55),
            "401",
            TENANT_A,
            WORKSPACE_A,
        ),
        (
            "e2",
            "sap_hcm",
            "PA0001",
            "incremental",
            "failed",
            NOW - timedelta(hours=4),
            NOW - timedelta(hours=3),
            "401",
            TENANT_A,
            WORKSPACE_A,
        ),
        (
            "e3",
            "sap_hcm",
            "PA0002",
            "incremental",
            "success",
            NOW - timedelta(hours=2),
            NOW - timedelta(hours=1),
            None,
            TENANT_A,
            WORKSPACE_A,
        ),
        (
            "e4",
            "sap_successfactors",
            "EmpJob",
            "incremental",
            "success",
            NOW - timedelta(hours=1),
            NOW - timedelta(minutes=50),
            None,
            TENANT_A,
            WORKSPACE_A,
        ),
        (
            "e5",
            "salesforce",
            "Opportunity",
            "incremental",
            "failed",
            NOW - timedelta(hours=1),
            NOW - timedelta(minutes=30),
            "unscoped",
            None,
            None,
        ),
    ]
    cur.executemany(
        "INSERT INTO public.extraction_runs VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        extraction,
    )


def _seed(dsn: str) -> None:
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            _seed_gold(cur)
            _seed_console(cur)
            for table in GOLD_TABLES:
                cur.execute(f"GRANT SELECT ON public.{table} TO omega_refinement_gold")
                cur.execute(f"SELECT public.omega_apply_gold_rls_for_table('{table}')")
            cur.execute(_staged_schema_sql())
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(scope="module")
def seeded_dsn(postgres_gold_with_native_rls: str) -> str:  # noqa: F811 — fixture
    _seed(postgres_gold_with_native_rls)
    return postgres_gold_with_native_rls


@pytest.fixture()
def gold_dsn(seeded_dsn: str, monkeypatch) -> str:
    role_dsn = seeded_dsn.replace(
        f"postgres:{POSTGRES_PASSWORD}",
        f"omega_refinement_gold:{GOLD_ROLE_PASSWORD}",
    )
    monkeypatch.setenv("GOLD_DATABASE_URL", role_dsn)
    return role_dsn


# ── Risk ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deal_slippage_counts_all_rows_beyond_the_preview_cap(gold_dsn):
    result = await query_deal_slippage(
        _user(TENANT_A, WORKSPACE_A), top_n=3, as_of=AS_OF
    )

    assert result.status == "ready", result.error
    assert result.deals == POPULATION + EXTRA, "must be the full COUNT(*), not the cap"
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
async def test_attrition_risk_population_reuses_talent_counts(gold_dsn):
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
async def test_employment_end_expiry_windows_exclude_sentinel_and_inactive(gold_dsn):
    result = await query_employment_end_expiry(
        _user(TENANT_A, WORKSPACE_A), as_of=AS_OF
    )

    assert result.status == "ready", result.error
    assert (result.within_30, result.within_60, result.within_90) == (4, 9, 15)
    assert result.active_filter_applied is True
    assert result.departments_top[0]["department_name"] == "Ventas"
    assert result.departments_top[0]["within_90"] == 11
    assert result.departments_top[0]["within_60"] == 5
    assert result.departments_top[1] == {
        "department_name": "Soporte",
        "within_30": 4,
        "within_60": 4,
        "within_90": 4,
    }

    isolated = await query_employment_end_expiry(
        _user(TENANT_B, WORKSPACE_B), as_of=AS_OF
    )
    assert isolated.status == "ready", isolated.error
    assert (isolated.within_30, isolated.within_90) == (1, 1)


# ── Finance ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_billable_hours_logged_sums_the_window(gold_dsn):
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
async def test_labor_cost_by_department_uses_last_closed_month(gold_dsn):
    result = await query_labor_cost_by_department(
        _user(TENANT_A, WORKSPACE_A), as_of=AS_OF
    )

    assert result.status == "ready", result.error
    assert result.period == date(2026, 8, 1)  # September rows are the open month
    assert result.departments_count == 2
    assert result.headcount == 12  # 7 SAP + 5 Data distinct userid
    assert result.total_hours == POPULATION
    assert result.total_cost == 3000 * 10 + 3000 * 5
    assert result.total_sunk_cost == 3000 * 2 + 3000 * 1
    assert [row["departamento"] for row in result.departments] == ["SAP", "Data"]
    assert result.departments[0]["cost"] == 30000.0
    assert result.departments[0]["headcount"] == 7

    missing = await query_labor_cost_by_department(
        _user(TENANT_B, WORKSPACE_B), as_of=AS_OF
    )
    assert missing.status == "unavailable"
    assert missing.error == "missing: dataset unavailable: costo_consultor_mensual"
    assert missing.supported is True


@pytest.mark.asyncio
async def test_project_margin_on_base_amounts(gold_dsn):
    result = await query_project_margin(
        _user(TENANT_A, WORKSPACE_A), months=2, top_n=1, as_of=AS_OF
    )

    assert result.status == "ready", result.error
    assert result.projects_count == 3  # OLD (May) is outside the window
    assert result.total_revenue_base == 100000 + 10000 + 10 * POPULATION
    assert result.total_cost_direct == 60000 + 14000 + 4 * POPULATION
    assert result.total_cost_sunk == 5000 + 1000 + 1 * POPULATION
    assert result.total_margin == 35000 - 5000 + 5 * POPULATION
    assert result.total_original_billing == 90000 + 0 + 10 * POPULATION
    assert result.original_currencies == ["MXN", "USD"]
    assert result.top_projects[0]["proyecto"] == "P-1"
    assert result.top_projects[0]["margin"] == 35000.0
    assert result.top_projects[0]["margin_pct"] == 35.0
    assert result.bottom_projects[0]["proyecto"] == "P-9"
    assert result.bottom_projects[0]["margin"] == -5000.0


# ── Operations ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_absence_rate_company_by_type_last_closed_month(gold_dsn):
    result = await query_absence_rate_company_by_type(
        _user(TENANT_A, WORKSPACE_A), as_of=AS_OF
    )

    assert result.status == "ready", result.error
    assert result.period == date(2026, 8, 1)
    assert result.working_days == 21
    assert result.headcount == 100
    assert result.total_days_workable == 210.0
    assert result.absence_rate == round(210 / (100 * 21), 4)
    assert result.types_count == 2
    assert result.by_type[0]["absence_type"] == "0100"
    assert result.by_type[0]["employees_affected"] == 40
    assert result.by_type[0]["rate"] == round(150 / 2100, 4)

    isolated = await query_absence_rate_company_by_type(
        _user(TENANT_B, WORKSPACE_B), as_of=AS_OF
    )
    assert isolated.status == "ready", isolated.error
    assert isolated.headcount == 10
    assert isolated.total_days_workable == 5.0


@pytest_asyncio.fixture()
async def console_pool(seeded_dsn: str, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", seeded_dsn)
    auth._POOL = None
    try:
        yield seeded_dsn
    finally:
        await auth.close_pool()
        auth._POOL = None


@pytest.mark.asyncio
async def test_pipeline_health_over_console_run_logs(console_pool):
    result = await query_pipeline_health(_user(TENANT_A, WORKSPACE_A), as_of=NOW)

    assert result.status == "ready", result.error
    by_cartridge = {row["cartridge_id"]: row for row in result.cartridges}
    sf = by_cartridge["sap_successfactors"]
    assert sf["failed_24h"] == 2
    assert sf["success_24h"] == 2  # pipeline_runs p3 + extraction_runs e4
    assert sf["failed_7d"] == 3  # p1, p2, p4 (p6 is older than 7 days)
    assert sf["success_7d"] == 3  # p3, p5, e4
    assert sf["failure_rate_7d"] == 0.5
    assert sf["last_failed_at"] == NOW - timedelta(hours=2, minutes=50)
    hcm = by_cartridge["sap_hcm"]
    assert hcm["failed_24h"] == 2
    assert hcm["success_24h"] == 1
    assert "salesforce" not in by_cartridge  # unscoped rows are invisible
    assert result.totals["failed_24h"] == 4
    assert result.cartridges_with_failures_24h == 2
    assert result.recent_failures[0]["cartridge_id"] == "sap_hcm"
    assert result.recent_failures[0]["error_message"] == "401"
    assert all(
        row["cartridge_id"] != "sap_successfactors"
        or row["error_message"] != "other tenant"
        for row in result.recent_failures
    )

    isolated = await query_pipeline_health(_user(TENANT_B, WORKSPACE_B), as_of=NOW)
    assert isolated.status == "ready", isolated.error
    assert [row["cartridge_id"] for row in isolated.cartridges] == [
        "sap_successfactors"
    ]
    assert isolated.totals["failed_24h"] == 1


@pytest.mark.asyncio
async def test_data_freshness_by_cartridge_over_console_run_logs(console_pool):
    result = await query_data_freshness_by_cartridge(
        _user(TENANT_A, WORKSPACE_A), sla_hours=24, as_of=NOW
    )

    assert result.status == "ready", result.error
    assert result.sla_source == "param"
    by_cartridge = {row["cartridge_id"]: row for row in result.cartridges}
    assert by_cartridge["sap_successfactors"]["hours_since_success"] == pytest.approx(
        50 / 60, abs=0.01
    )
    assert by_cartridge["sap_successfactors"]["success_runs"] == 3
    assert by_cartridge["sap_hcm"]["hours_since_success"] == 1.0
    assert by_cartridge["replicon"]["hours_since_success"] == 30.0
    assert by_cartridge["replicon"]["exceeds_sla"] is True
    assert result.exceeding_sla == 1
    assert "salesforce" not in by_cartridge

    unscoped = await query_data_freshness_by_cartridge(
        {"tenant_id": TENANT_A}, as_of=NOW
    )
    assert unscoped.status == "unavailable"
    assert unscoped.error == "no_permission: active workspace is required"
