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
    assert re.search(
        r"NULLIF\(TRIM\(CAST\(approved_by AS VARCHAR\)\), ''\) IS NOT NULL",
        benchmark_cte,
    )
    assert "NOT LIKE 'system%'" in benchmark_cte
    assert "END AS approval_valid" in benchmark_cte
    assert "WHERE enabled = TRUE" in benchmark_cte
    assert "ORDER BY approval_valid DESC" in benchmark_cte
    assert "AND approved = TRUE" not in benchmark_cte
    assert "benchmark_approval_valid" in sql
    assert "PERCENT_RANK() OVER" in sql
    assert "PARTITION BY COALESCE(CAST(tenant_id AS VARCHAR), '')" in sql
    assert "ELSE 'insufficient_data'" in sql


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
