from __future__ import annotations

import re
import importlib.util
import runpy
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
MCP_GUARD = ROOT / "mcp-infra/app/operational_truth.py"


def _wip_sql() -> dict[str, str]:
    payload = yaml.safe_load(REPLICON_KBS.read_text(encoding="utf-8"))
    configs = {}
    for item in payload["knowledge_bits"]:
        kb_id = str(item.get("id") or "")
        if kb_id not in WIP_IDS:
            continue
        sql = (REPLICON_KBS.parent / item["sql_file"]).read_text(encoding="utf-8")
        if "__WIP_MENSUAL_V3__" in sql:
            monthly = (
                (REPLICON_KBS.parent / "sql/kb_wip_mensual_v3.sql")
                .read_text(encoding="utf-8")
                .strip()
                .removesuffix(";")
            )
            sql = sql.replace("__WIP_MENSUAL_V3__", monthly)
        configs[kb_id] = sql
    return configs


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


def test_talent_readiness_never_uses_dataset_rows_as_approval_authority() -> None:
    sql = READINESS.read_text(encoding="utf-8")
    benchmark_cte = sql.split("benchmark AS (", 1)[1].split(
        "),\nbenchmark_raw AS (", 1
    )[0]

    assert "SELECT *, FALSE AS approval_valid" in benchmark_cte
    assert "WHEN approved = TRUE" not in benchmark_cte
    assert "TRY_CAST(approved_at" not in benchmark_cte
    assert "TRY_CAST(approved_by" not in benchmark_cte
    assert "approval_actor_source =" not in benchmark_cte
    assert "approval_evidence_ref)" not in benchmark_cte
    assert "approval_authorization_ref)" not in benchmark_cte
    assert "WHERE enabled = TRUE" in benchmark_cte
    assert "ORDER BY materialized_at DESC" in benchmark_cte
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
    assert "select *, false as approval_valid" in readiness
    assert "approval_recorded_by_server = true" not in readiness
    assert "approval_authorization_verified = true" not in readiness
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
        assert "then converted_billed_amount_usd end as billed_amount_usd" in lowered
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


def test_generic_readers_block_noncurrent_replicon_wip_artifacts() -> None:
    spec = importlib.util.spec_from_file_location("mcp_operational_truth", MCP_GUARD)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    reason = module.replicon_artifact_block_reason

    assert reason(
        "SELECT * FROM read_parquet('s3://bucket/knowledge_bits/replicon/wip_mensual/x.parquet')",
        cartridge_id="replicon",
    )
    assert reason(
        "SELECT * FROM knowledge_bits_quarantine.replicon_wip_resumen",
        postgres=True,
    )
    assert reason(
        "SELECT * FROM knowledge_bits_history.replicon_wip_mensual",
        postgres=True,
    )
    assert not reason(
        "SELECT * FROM knowledge_bits.replicon_wip_mensual",
        postgres=True,
    )


def test_generic_replicon_reader_resolves_custom_reserved_paths_fail_closed() -> None:
    module = runpy.run_path(str(MCP_GUARD))

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, _sql):
            return None

        def fetchall(self):
            return [("customer_table", "customer/path")]

    class Connection(Cursor):
        def cursor(self):
            return Cursor()

    block = module["replicon_generic_query_block_reason"]
    reason = block(
        "SELECT * FROM read_parquet('s3://bucket/customer/path/legacy.parquet')",
        cartridge_id="replicon",
        connection_factory=Connection,
    )
    assert reason == "noncurrent_replicon_wip_artifact"
    assert (
        block(
            "SELECT 1",
            cartridge_id="replicon",
            connection_factory=lambda: (_ for _ in ()).throw(RuntimeError("db down")),
        )
        == "replicon_wip_provenance_unavailable"
    )
