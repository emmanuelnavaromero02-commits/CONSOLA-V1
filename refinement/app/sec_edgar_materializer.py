from __future__ import annotations

from typing import Any

try:
    from app.duckdb_engine import _sql_quote, validate_safe_identifier
    from app.sec_edgar_manifest import FACT_CONFIG, SECInsufficientData, complete_manifests, latest_manifest
except ModuleNotFoundError:
    from refinement.app.duckdb_engine import _sql_quote, validate_safe_identifier
    from refinement.app.sec_edgar_manifest import FACT_CONFIG, SECInsufficientData, complete_manifests, latest_manifest


SEC_DATASETS = {
    "sec_company_metadata_latest",
    "sec_company_facts_normalized",
    "sec_company_quality",
    "sec_market_context",
}


def materialize_sec_dataset(engine: Any, ds: dict, user_context: dict | None = None) -> dict:
    name = str(ds["name"])
    layer = str(ds.get("layer") or "silver")
    validate_safe_identifier(name, "dataset")
    if name not in SEC_DATASETS:
        raise ValueError(f"Unsupported SEC EDGAR dataset: {name}")
    if layer not in {"silver", "gold"}:
        raise ValueError("Invalid dataset layer")

    sql, source_entity, source_load_date, source_batch_id = _sql_for(engine, name, user_context)
    with engine._duckdb_lock:
        con = engine._conn()
        if layer == "gold":
            storage_uri, row_count = _materialize_gold(engine, con, name, sql, user_context)
        else:
            effective = engine._ensure_scope_columns(con, sql, user_context)
            storage_uri = engine._copy_to_parquet(con, effective, engine._snapshot_path("silver", "sec_edgar", name, user_context))
            row_count = con.execute(f"SELECT COUNT(*) FROM read_parquet('{storage_uri}')").fetchone()[0]
        schema_fields = _schema_fields(engine, con, layer, name, storage_uri, user_context)

    engine._write_lineage(
        silver_name=name,
        cartridge_id="sec_edgar",
        source_entity=source_entity,
        source_load_date=source_load_date,
        source_batch_id=source_batch_id,
        sql_def=str(ds.get("sql_def") or ds.get("sql") or ""),
        column_mapping=ds.get("column_mapping", {}),
        layer=layer,
        row_count=int(row_count or 0),
        storage_uri=storage_uri,
    )
    engine._prune_snapshots(layer, "sec_edgar", name, user_context)
    engine._update_catalog(name, layer, "sec_edgar", schema_fields, ds.get("column_mapping", {}), ds.get("description", ""), user_context)
    return {"name": name, "layer": layer, "row_count": int(row_count or 0), "storage_uri": storage_uri}


def _materialize_gold(engine: Any, con: Any, name: str, sql: str, user_context: dict | None) -> tuple[str, int]:
    tenant, workspace = engine._scope_values(user_context)
    if not (tenant and workspace):
        raise ValueError("Gold materialization requires tenant_id and workspace_id")
    effective = engine._ensure_scope_columns(con, sql, user_context)
    table = f"gold_{name}"
    validate_safe_identifier(table, "table")
    engine._pg_gold_attach(con, user_context)
    engine._ensure_scoped_gold_table(con, table, effective)
    row_count = engine._replace_scoped_gold_rows(con, table, effective, tenant, workspace)
    engine._apply_gold_rls(table)
    snapshot_uri = engine._copy_scoped_gold_table_snapshot(
        con, table, engine._snapshot_path("gold", "sec_edgar", name, user_context), tenant, workspace, user_context
    )
    return snapshot_uri or f"postgres_gold:{table}", row_count


def _sql_for(engine: Any, name: str, user_context: dict | None) -> tuple[str, str, str | None, str | None]:
    if name == "sec_company_metadata_latest":
        manifests = complete_manifests(engine.storage, "company_metadata", user_context)
        if not manifests:
            raise SECInsufficientData("no complete SEC metadata manifests")
        latest = latest_manifest(manifests)
        return _metadata_sql([item.parquet_uri for item in manifests]), "raw/sec_edgar/company_metadata", latest.load_date, latest.run_id
    if name == "sec_company_facts_normalized":
        manifests = complete_manifests(engine.storage, "company_facts", user_context)
        if not manifests:
            raise SECInsufficientData("no complete SEC facts manifests")
        latest = latest_manifest(manifests)
        return _facts_sql([item.parquet_uri for item in manifests]), "raw/sec_edgar/company_facts", latest.load_date, latest.run_id
    if name == "sec_company_quality":
        facts = _latest_uri(engine, "silver", "sec_company_facts_normalized", user_context)
        meta = _latest_uri(engine, "silver", "sec_company_metadata_latest", user_context)
        return _quality_sql(facts, meta), "silver/sec_edgar/sec_company_facts_normalized", None, None
    quality = _latest_uri(engine, "silver", "sec_company_quality", user_context)
    facts = _latest_uri(engine, "silver", "sec_company_facts_normalized", user_context)
    return _gold_sql(facts, quality), "silver/sec_edgar/sec_company_quality", None, None


def _read_parquet_sql(uris: list[str]) -> str:
    return "read_parquet([" + ", ".join(_sql_quote(uri) for uri in uris) + "], hive_partitioning=true, union_by_name=true)"


def _config_values_sql() -> str:
    rows = ", ".join(
        "("
        + ", ".join(
            [
                _sql_quote(str(item["cik"])),
                _sql_quote(str(item["ticker"])),
                _sql_quote(str(item["metric_name"])),
                _sql_quote(str(item["unit"])),
                str(int(item["freshness_sla_days"])),
            ]
        )
        + ")"
        for item in FACT_CONFIG
    )
    return f"(VALUES {rows}) AS cfg(cik, ticker, metric_name, expected_unit, freshness_sla_days)"


def _metadata_sql(uris: list[str]) -> str:
    return f"""
WITH src AS (SELECT * FROM {_read_parquet_sql(uris)}),
ranked AS (
  SELECT cik, company_name, tickers, exchanges, entity_type, sic, sic_description, fiscal_year_end,
         _source_authority, _source_url, _source_host, _request_hash, _payload_hash, _run_id,
         _retrieved_at, load_date, batch_id,
         ROW_NUMBER() OVER (PARTITION BY cik ORDER BY _retrieved_at DESC, load_date DESC, batch_id DESC) AS rn
  FROM src WHERE cik IS NOT NULL AND cik <> ''
)
SELECT * EXCLUDE (rn), CURRENT_TIMESTAMP AS materialized_at
FROM ranked WHERE rn = 1
"""


def _facts_sql(uris: list[str]) -> str:
    cfg = _config_values_sql()
    return f"""
WITH cfg AS (SELECT * FROM {cfg}),
src AS (SELECT * FROM {_read_parquet_sql(uris)}),
typed AS (
  SELECT src.cik, src.ticker, src.company_name, src.metric_name, src.taxonomy, src.concept,
         src.country_code, CAST(src.end_date AS DATE) AS end_date, CAST(NULLIF(src.start_date, '') AS DATE) AS start_date,
         src.fiscal_year, src.fiscal_period, src.form, src.filed, src.accession_number, src.frame,
         TRY_CAST(src.value_raw AS DOUBLE) AS value_decimal, COALESCE(src.unit, cfg.expected_unit) AS unit,
         cfg.freshness_sla_days, src._source_authority, src._source_url, src._source_host,
         src._request_hash, src._payload_hash, src._run_id, src._retrieved_at, src.load_date, src.batch_id
  FROM src JOIN cfg ON cfg.cik = src.cik AND cfg.metric_name = src.metric_name
),
ranked AS (
  SELECT *, ROW_NUMBER() OVER (
    PARTITION BY cik, metric_name, end_date, fiscal_year, fiscal_period, accession_number
    ORDER BY _retrieved_at DESC, load_date DESC, batch_id DESC
  ) AS rn FROM typed
)
SELECT * EXCLUDE (rn),
       (unit IS NOT NULL AND unit <> '' AND _source_host = 'data.sec.gov') AS schema_valid,
       (value_decimal IS NOT NULL) AS value_valid,
       CURRENT_TIMESTAMP AS materialized_at
FROM ranked WHERE rn = 1
"""


def _quality_sql(facts_uri: str, meta_uri: str) -> str:
    cfg = _config_values_sql()
    return f"""
WITH cfg AS (SELECT * FROM {cfg}),
facts AS (SELECT * FROM read_parquet({_sql_quote(facts_uri)}, union_by_name=true)),
meta AS (SELECT * FROM read_parquet({_sql_quote(meta_uri)}, union_by_name=true)),
agg AS (
  SELECT cfg.cik, cfg.ticker, cfg.metric_name, cfg.expected_unit AS unit, cfg.freshness_sla_days,
         COUNT(facts.end_date) AS observed_count, MAX(facts.end_date) AS latest_observation_date,
         SUM(CASE WHEN facts.schema_valid THEN 1 ELSE 0 END) AS schema_valid_count,
         SUM(CASE WHEN facts.value_valid THEN 1 ELSE 0 END) AS value_valid_count,
         MAX(CASE WHEN meta._source_host = 'data.sec.gov' AND meta._source_authority = 'SEC' THEN 1.0 ELSE 0.0 END) AS source_trust_score
  FROM cfg
  LEFT JOIN facts ON facts.cik = cfg.cik AND facts.metric_name = cfg.metric_name
  LEFT JOIN meta ON meta.cik = cfg.cik
  GROUP BY cfg.cik, cfg.ticker, cfg.metric_name, cfg.expected_unit, cfg.freshness_sla_days
),
components AS (
  SELECT *, DATE_DIFF('day', latest_observation_date, CURRENT_DATE) AS age_days,
    CASE WHEN observed_count > 0 AND schema_valid_count = observed_count THEN 1.0 ELSE 0.0 END AS schema_validity_score,
    CASE WHEN latest_observation_date IS NULL THEN 0.0
         WHEN DATE_DIFF('day', latest_observation_date, CURRENT_DATE) <= freshness_sla_days THEN 1.0
         WHEN DATE_DIFF('day', latest_observation_date, CURRENT_DATE) <= freshness_sla_days * 2 THEN 0.5
         ELSE 0.0 END AS freshness_score,
    CASE WHEN observed_count > 0 THEN value_valid_count::DOUBLE / observed_count ELSE 0.0 END AS value_validity_score,
    CASE WHEN observed_count > 0 AND latest_observation_date IS NOT NULL THEN 1.0 ELSE 0.0 END AS completeness_score
  FROM agg
),
scored AS (
  SELECT *, ROUND((0.30 * source_trust_score) + (0.25 * schema_validity_score)
    + (0.20 * freshness_score) + (0.15 * value_validity_score)
    + (0.10 * completeness_score), 4) AS confidence
  FROM components
)
SELECT *,
  CASE WHEN observed_count = 0 THEN 'insufficient_data'
       WHEN age_days > freshness_sla_days THEN 'stale'
       WHEN confidence >= 0.80 THEN 'ready'
       ELSE 'partial' END AS status,
  0.30 AS source_trust_weight, 0.25 AS schema_validity_weight,
  0.20 AS freshness_weight, 0.15 AS value_validity_weight,
  0.10 AS completeness_weight,
  CURRENT_TIMESTAMP AS materialized_at
FROM scored
"""


def _gold_sql(facts_uri: str, quality_uri: str) -> str:
    return f"""
WITH facts AS (SELECT * FROM read_parquet({_sql_quote(facts_uri)}, union_by_name=true)),
quality AS (SELECT * FROM read_parquet({_sql_quote(quality_uri)}, union_by_name=true)),
latest AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY cik, metric_name ORDER BY end_date DESC, _retrieved_at DESC) AS rn
  FROM facts
)
SELECT quality.cik, quality.ticker, quality.metric_name, latest.end_date AS as_of,
       latest.value_decimal AS value, quality.unit, latest.country_code,
       quality.confidence, quality.status AS freshness_status,
       (quality.confidence >= 0.80 AND quality.status = 'ready') AS usable,
       latest._source_authority AS source_authority, latest._source_url AS source_url,
       latest._source_host AS source_host, latest._request_hash AS request_hash,
       latest._payload_hash AS payload_hash, latest._run_id AS run_id,
       CURRENT_TIMESTAMP AS materialized_at
FROM quality
LEFT JOIN latest ON latest.cik = quality.cik AND latest.metric_name = quality.metric_name AND latest.rn = 1
"""


def _latest_uri(engine: Any, layer: str, name: str, user_context: dict | None) -> str:
    uri = engine._latest_materialized_uri(layer, "sec_edgar", name, user_context)
    if not uri:
        raise SECInsufficientData(f"missing materialized dependency: {layer}/sec_edgar/{name}")
    return uri


def _schema_fields(engine: Any, con: Any, layer: str, name: str, storage_uri: str, user_context: dict | None) -> list[dict]:
    if layer == "gold" and storage_uri.startswith("postgres_gold:"):
        return engine._pg_table_schema(storage_uri.split(":", 1)[1], user_context)
    return engine._parquet_schema_fields(con, storage_uri)
