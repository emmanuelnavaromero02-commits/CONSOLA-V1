from __future__ import annotations

from pathlib import Path

import psycopg2
import pytest

from app.services.intelligence.successfactors_talent_population import (
    query_desempeno_cohort_counts,
    query_nine_box_box_count,
    query_nine_box_cell_counts,
    query_talent_population_counts,
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

POPULATION = 6000
INSUFFICIENT = 10


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
async def test_population_counts_cover_all_rows_beyond_the_preview_cap(
    postgres_gold_with_native_rls: str,  # noqa: F811 — pytest fixture
    monkeypatch,
):
    conn = psycopg2.connect(postgres_gold_with_native_rls)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE public.gold_sap_successfactors_talent_readiness (
                    tenant_id text NOT NULL,
                    workspace_id text NOT NULL,
                    invalid_score_input boolean NOT NULL,
                    readiness_status text,
                    source_mode text,
                    readiness_score numeric,
                    competency_score numeric,
                    performance_score numeric,
                    aspiration_score numeric
                )
                """
            )
            cur.execute(
                """
                INSERT INTO public.gold_sap_successfactors_talent_readiness
                SELECT %s, %s, FALSE, 'ready', 'cpa_real', 81, 82, 83, 84
                  FROM generate_series(1, %s)
                """,
                (TENANT_A, WORKSPACE_A, POPULATION),
            )
            cur.execute(
                """
                INSERT INTO public.gold_sap_successfactors_talent_readiness
                SELECT %s, %s, TRUE, 'ready', 'cpa_real', 81, 82, 83, 84
                  FROM generate_series(1, %s)
                """,
                (TENANT_A, WORKSPACE_A, INSUFFICIENT),
            )
            cur.execute(
                """
                INSERT INTO public.gold_sap_successfactors_talent_readiness
                SELECT %s, %s, FALSE, 'ready', 'cpa_real', 81, 82, 83, 84
                  FROM generate_series(1, 3)
                """,
                (TENANT_B, WORKSPACE_B),
            )
            cur.execute(
                """
                CREATE TABLE public.gold_sap_successfactors_talent_9box (
                    tenant_id text NOT NULL,
                    workspace_id text NOT NULL,
                    invalid_score_input boolean NOT NULL,
                    performance_score numeric,
                    potential_score numeric,
                    box_status text,
                    box_key text,
                    performance_band_available text
                )
                """
            )
            cur.execute(
                """
                INSERT INTO public.gold_sap_successfactors_talent_9box
                SELECT %s, %s, FALSE, 82, 75, 'ready', 'star', NULL
                  FROM generate_series(1, %s)
                """,
                (TENANT_A, WORKSPACE_A, POPULATION),
            )
            cur.execute(
                """
                INSERT INTO public.gold_sap_successfactors_talent_9box
                SELECT %s, %s, FALSE, 45, 40, 'blocked', 'core', 'low'
                  FROM generate_series(1, %s)
                """,
                (TENANT_A, WORKSPACE_A, INSUFFICIENT),
            )
            cur.execute(
                """
                INSERT INTO public.gold_sap_successfactors_talent_9box
                SELECT %s, %s, FALSE, 82, 75, 'ready', 'star', NULL
                  FROM generate_series(1, 3)
                """,
                (TENANT_B, WORKSPACE_B),
            )
            for table in (
                "gold_sap_successfactors_talent_readiness",
                "gold_sap_successfactors_talent_9box",
            ):
                cur.execute(
                    f"GRANT SELECT ON public.{table} TO omega_refinement_gold"
                )
                cur.execute(f"SELECT public.omega_apply_gold_rls_for_table('{table}')")
            cur.execute(_staged_schema_sql())
        conn.commit()
    finally:
        conn.close()

    role_dsn = postgres_gold_with_native_rls.replace(
        f"postgres:{POSTGRES_PASSWORD}",
        f"omega_refinement_gold:{GOLD_ROLE_PASSWORD}",
    )
    monkeypatch.setenv("GOLD_DATABASE_URL", role_dsn)

    counts = await query_talent_population_counts(_user(TENANT_A, WORKSPACE_A))
    assert counts["status"] == "ready", counts["error"]
    assert counts["readiness_calculable"] == POPULATION, (
        "the population total must be the full COUNT(*), not the 5,000-row cap"
    )
    assert counts["readiness_insufficient"] == INSUFFICIENT
    assert counts["nine_box_available"] == POPULATION

    cohort = await query_desempeno_cohort_counts(_user(TENANT_A, WORKSPACE_A))
    assert cohort["status"] == "ready", cohort["error"]
    assert cohort["count"] == POPULATION + INSUFFICIENT
    assert cohort["band_counts"] == {
        "high": POPULATION,
        "medium": 0,
        "low": INSUFFICIENT,
    }

    box = await query_nine_box_box_count(_user(TENANT_A, WORKSPACE_A), "star")
    assert box["status"] == "ready", box["error"]
    assert box["count"] == POPULATION

    cells = await query_nine_box_cell_counts(_user(TENANT_A, WORKSPACE_A))
    assert cells["status"] == "ready", cells["error"]
    by_box = {row["box_key"]: row for row in cells["rows"]}
    assert by_box["star"]["employee_count"] == POPULATION
    assert by_box["star"]["ready_count"] == POPULATION
    assert by_box["star"]["box_status"] == "ready"
    assert by_box["core"]["employee_count"] == INSUFFICIENT
    assert by_box["core"]["ready_count"] == 0
    assert by_box["core"]["box_status"] == "blocked"

    isolated = await query_talent_population_counts(_user(TENANT_B, WORKSPACE_B))
    assert isolated["status"] == "ready", isolated["error"]
    assert isolated["readiness_calculable"] == 3
    assert isolated["readiness_insufficient"] == 0

    cross = await query_talent_population_counts(_user(TENANT_A, WORKSPACE_B))
    assert cross["readiness_calculable"] in (None, 0)
    assert cross["status"] != "ready" or cross["readiness_calculable"] == 0
