-- sap_b1_oitm_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OITM"]
-- description: Item master: inventory / sales / purchase flags, batch management, default warehouse.

WITH latest AS (
    -- Estado ACTUAL por clave sobre TODO el histórico bronze. La entidad es
    -- incremental: quedarse con MAX(load_date) colapsaría la población al
    -- delta del día. Un borrado en la fuente no se refleja hasta una carga
    -- completa; ver README del cartucho.
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, ItemCode
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OITM/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
)
SELECT
    CAST(ItemCode AS VARCHAR)                   AS item_code,
    CAST(ItemName AS VARCHAR)                   AS item_name,
    CAST(ItmsGrpCod AS BIGINT)                  AS itms_grp_cod,
    CAST(InvntItem AS VARCHAR)                  AS invnt_item,
    CAST(SellItem AS VARCHAR)                   AS sell_item,
    CAST(PrchseItem AS VARCHAR)                 AS prchse_item,
    CAST(ManBtchNum AS VARCHAR)                 AS man_btch_num,
    CAST(DfltWH AS VARCHAR)                     AS dflt_wh,
    CAST(AvgPrice AS DECIMAL(19,6))             AS avg_price,
    CAST(LastPurPrc AS DECIMAL(19,6))           AS last_pur_prc,
    CAST(validFor AS VARCHAR)                   AS valid_for,
    CAST(frozenFor AS VARCHAR)                  AS frozen_for,
    CAST(CreateDate AS TIMESTAMP)               AS create_date,
    CAST(UpdateDate AS TIMESTAMP)               AS update_date,
    CAST(UpdateTS AS BIGINT)                    AS update_ts,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM latest
ORDER BY company, item_code
