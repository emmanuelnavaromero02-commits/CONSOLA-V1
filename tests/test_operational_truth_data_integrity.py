from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = (
    ROOT
    / "cartridges/sap_successfactors/datasets/sap_successfactors_talent_benchmark_internal.sql"
)
READINESS = (
    ROOT
    / "cartridges/sap_successfactors/datasets/sap_successfactors_talent_readiness.sql"
)
NINE_BOX = (
    ROOT / "cartridges/sap_successfactors/datasets/sap_successfactors_talent_9box.sql"
)
HISTORICAL_REPAIR = (
    ROOT / "infra/init/99zo_sap_successfactors_talent_operational_truth_repair.sql"
)
BENCHMARK_INIT_PATHS = (
    ROOT / "infra/init/99zj_sap_successfactors_talent_operational_contract_v2.sql",
    ROOT / "infra/init/99zm_sap_successfactors_talent_operational_activation.sql",
    ROOT / "infra/init/99zn_sap_successfactors_talent_runtime_repair.sql",
    ROOT / "infra/init_gold/36_sap_successfactors_talent_benchmark_repair.sql",
)
REPLICON_KBS = ROOT / "cartridges/replicon/app/config/knowledge_bits.yaml"
WIP_IDS = {"kb_wip_mensual", "kb_wip_resumen"}


def _wip_sql() -> dict[str, str]:
    payload = yaml.safe_load(REPLICON_KBS.read_text(encoding="utf-8"))
    return {
        str(item["id"]): str(item["sql"])
        for item in payload["knowledge_bits"]
        if item.get("id") in WIP_IDS
    }


def test_packaged_talent_benchmark_is_unreviewed_system_default() -> None:
    sql = BENCHMARK.read_text(encoding="utf-8")
    lowered = sql.lower()

    assert "false as approved" in lowered
    assert "null as approved_by" in lowered
    assert "null as approved_at" in lowered
    assert "system_default" in lowered
    assert "unreviewed" in lowered
    assert "fallback operativo aprobado" not in lowered
    assert "benchmark sectorial" not in lowered
    assert "benchmark externo" not in lowered
    assert "benchmark aprendido" not in lowered


def test_talent_readiness_requires_real_actor_and_date_for_approval() -> None:
    sql = READINESS.read_text(encoding="utf-8")
    benchmark_cte = sql.split("benchmark AS (", 1)[1].split(
        "),\nbenchmark_raw AS (", 1
    )[0]

    assert "WHEN approved = TRUE" in benchmark_cte
    assert "TRY_CAST(approved_at AS TIMESTAMP) IS NOT NULL" in benchmark_cte
    assert "TRY_CAST(approved_by AS BIGINT) > 0" in benchmark_cte
    assert "NOT LIKE 'system%'" not in benchmark_cte
    assert "approval_actor_source = 'server'" in benchmark_cte
    assert "approval_evidence_ref" in benchmark_cte
    assert "approval_authorization_ref" in benchmark_cte
    assert "END AS approval_valid" in benchmark_cte
    assert "WHERE enabled = TRUE" in benchmark_cte
    assert "ORDER BY approval_valid DESC" in benchmark_cte
    assert "AND approved = TRUE" not in benchmark_cte
    assert "benchmark_approval_valid" in sql
    assert "PERCENT_RANK() OVER" in sql
    assert "PARTITION BY COALESCE(CAST(tenant_id AS VARCHAR), '')" in sql
    assert "ELSE 'insufficient_data'" in sql


def test_talent_benchmark_needs_durable_server_side_approval() -> None:
    benchmark = BENCHMARK.read_text(encoding="utf-8").lower()
    readiness = READINESS.read_text(encoding="utf-8").lower()

    assert "null as approval_evidence_ref" in benchmark
    assert "null as approval_authorization_ref" in benchmark
    assert "null as approval_actor_source" in benchmark
    assert "false as approval_recorded_by_server" in benchmark
    assert "false as approval_authorization_verified" in benchmark
    assert "approval_recorded_by_server = true" in readiness
    assert "approval_authorization_verified = true" in readiness
    assert "benchmark_approval_valid" in readiness
    assert "benchmark_provenance_status" in readiness
    assert (
        "when source_mode = 'benchmark_internal' and benchmark_approval_valid then null"
        in readiness
    )


def test_every_packaged_or_init_benchmark_path_is_unreviewed() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8") for path in (BENCHMARK, *BENCHMARK_INIT_PATHS)
    ).lower()

    assert "true as approved" not in combined
    assert "talent_benchmark_internal.v1.approved" not in combined
    assert "system:tenant_admin_request" not in combined
    assert "false as approved" in combined
    assert "approval_recorded_by_server" in combined
    assert "approval_authorization_verified" in combined
    assert "benchmark_internal_unreviewed" in combined
    gold_repair = BENCHMARK_INIT_PATHS[-1].read_text(encoding="utf-8").lower()
    assert "trim(cast(approved_by as text)) ~ ''^[1-9][0-9]*$''" in gold_repair


def test_talent_runtime_has_no_decorative_observation_substitutes() -> None:
    sql = (
        READINESS.read_text(encoding="utf-8") + NINE_BOX.read_text(encoding="utf-8")
    ).lower()

    for value in ("35.0", "45.0", "70.0", "30.0"):
        assert value not in sql
    assert "when source_mode = 'insufficient_data' then 4" not in sql
    assert "benchmark_raw_score" in sql
    assert "when benchmark_approval_valid" in sql
    assert "benchmark_performance_proxy" in sql
    assert "null::double as benchmark_performance_proxy" in sql
    assert "null::double as benchmark_potential_proxy" in sql


def test_historical_talent_projection_is_fail_closed_until_recomputed() -> None:
    sql = HISTORICAL_REPAIR.read_text(encoding="utf-8").lower()

    assert "benchmark_approval_valid" in sql
    assert "benchmark_provenance_status" in sql
    assert "insufficient_data" in sql
    assert "stale_unapproved_benchmark" in sql
    assert "pggold" in sql
    assert "public" in sql
    assert "schema_migrations" in sql


def test_replicon_wip_has_no_fabricated_currency_or_rate_fallbacks() -> None:
    configs = _wip_sql()

    assert set(configs) == WIP_IDS
    for sql in configs.values():
        lowered = sql.lower()
        assert not re.search(r"(?<![\d.])0\.05(?!\d)", sql)
        assert "coalesce(fx.mxn_to_usd" not in lowered
        assert not re.search(r"coalesce\(rm\.rate_usd,\s*rd\.rate_usd,\s*0", lowered)
        assert "else billed_amount" not in lowered
        assert "missing_fx" in lowered
        assert "insufficient_data" in lowered
        assert "original_billed_usd" in lowered
        assert "original_billed_mxn" in lowered
        assert "billing_data_status" in lowered


def test_replicon_wip_only_emits_financial_signals_with_usable_inputs() -> None:
    configs = _wip_sql()

    for sql in configs.values():
        lowered = sql.lower()
        assert "as converted_billed_amount_usd" in lowered
        assert (
            "when billing_data_status = 'ready' then converted_billed_amount_usd"
            in lowered
        )
        assert "when financial_status = 'ready'" in lowered
        assert "billable_amount_usd - converted_billed_amount_usd" in lowered
        assert "converted_billed_amount_usd / nullif(billable_amount_usd, 0)" in lowered
        assert "billing_rate_usd" in lowered
        assert "billable_amount_usd" in lowered
        assert "isfinite" in lowered
        assert re.search(r"mxn_to_usd[^\n]*> 0|avg_rate[^\n]*> 0", lowered)

    summary = configs["kb_wip_resumen"].lower()
    assert ">= 0.9" in summary
    assert ">= 0.5" in summary


def test_replicon_knowledge_bits_stays_within_file_size_contract() -> None:
    assert len(REPLICON_KBS.read_text(encoding="utf-8").splitlines()) <= 300
