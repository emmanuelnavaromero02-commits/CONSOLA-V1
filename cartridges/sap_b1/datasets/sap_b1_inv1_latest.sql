-- sap_b1_inv1_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/INV1"]
-- description: A/R invoices lines, read through OINV: quantities, prices, line totals in three currencies, base/target links.

WITH versions AS (
    -- Estado ACTUAL por clave sobre TODO el histórico bronze; después solo
    -- las líneas que llevan la marca más reciente de su cabecera: una línea
    -- borrada del documento desaparece en cuanto la cabecera se relee.
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry, LineNum
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/INV1/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
),
current_lines AS (
    SELECT *
    FROM (
        SELECT *,
               MAX(_source_updated_at) OVER (PARTITION BY _company, DocEntry) AS _header_stamp
        FROM versions
    )
    WHERE _source_updated_at IS NULL OR _source_updated_at = _header_stamp
)
SELECT
    CAST(DocEntry AS BIGINT)                    AS doc_entry,
    CAST(LineNum AS BIGINT)                     AS line_num,
    CAST(TargetType AS BIGINT)                  AS target_type,
    CAST(TrgetEntry AS BIGINT)                  AS trget_entry,
    CAST(BaseType AS BIGINT)                    AS base_type,
    CAST(BaseEntry AS BIGINT)                   AS base_entry,
    CAST(BaseLine AS BIGINT)                    AS base_line,
    CAST(LineStatus AS VARCHAR)                 AS line_status,
    CAST(ItemCode AS VARCHAR)                   AS item_code,
    CAST(Dscription AS VARCHAR)                 AS dscription,
    CAST(Quantity AS DECIMAL(19,6))             AS quantity,
    CAST(OpenQty AS DECIMAL(19,6))              AS open_qty,
    CAST(Price AS DECIMAL(19,6))                AS price,
    CAST(PriceBefDi AS DECIMAL(19,6))           AS price_bef_di,
    CAST(Currency AS VARCHAR)                   AS currency,
    CAST(Rate AS DECIMAL(19,6))                 AS rate,
    CAST(DiscPrcnt AS DECIMAL(19,6))            AS disc_prcnt,
    CAST(LineTotal AS DECIMAL(19,6))            AS line_total,
    CAST(TotalFrgn AS DECIMAL(19,6))            AS total_frgn,
    CAST(TotalSumSy AS DECIMAL(19,6))           AS total_sum_sy,
    CAST(GrssProfit AS DECIMAL(19,6))           AS grss_profit,
    CAST(GrssProfFC AS DECIMAL(19,6))           AS grss_prof_fc,
    CAST(GrssProfSC AS DECIMAL(19,6))           AS grss_prof_sc,
    CAST(StockPrice AS DECIMAL(19,6))           AS stock_price,
    CAST(WhsCode AS VARCHAR)                    AS whs_code,
    CAST(ShipDate AS TIMESTAMP)                 AS ship_date,
    CAST(VatPrcnt AS DECIMAL(19,6))             AS vat_prcnt,
    CAST(VatSum AS DECIMAL(19,6))               AS vat_sum,
    CAST(AcctCode AS VARCHAR)                   AS acct_code,
    CAST(OcrCode AS VARCHAR)                    AS ocr_code,
    CAST(LineType AS VARCHAR)                   AS line_type,
    CAST(TreeType AS VARCHAR)                   AS tree_type,
    CAST(ObjType AS VARCHAR)                    AS obj_type,
    CAST(VisOrder AS BIGINT)                    AS vis_order,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM current_lines
ORDER BY company, doc_entry, line_num
