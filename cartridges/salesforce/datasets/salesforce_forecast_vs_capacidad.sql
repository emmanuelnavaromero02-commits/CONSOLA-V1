-- salesforce_forecast_vs_capacidad  (gold)  cartridge: salesforce   [CROSS-CARTRIDGE]
-- sources: ["silver/salesforce/salesforce_opportunity_latest", "gold/replicon/costo_consultor_mensual"]
-- description: Cruza el forecast ponderado de ventas (Salesforce) contra la capacidad operativa
-- (Replicon). demanda_horas_estimada usa una tarifa supuesta de 150 USD/hora (no hay costo real
-- cargado); holgura_horas = capacidad - demanda. Si falta el gold de Replicon, capacidad va NULL.
WITH sf AS (
    SELECT
        CAST(DATE_TRUNC('month', close_date) AS DATE)        AS mes,
        SUM(amount * probability / 100.0)                    AS forecast_ponderado_usd
    FROM read_parquet('s3://{bucket}/silver/salesforce/salesforce_opportunity_latest/**/*.parquet',
                      hive_partitioning = true, union_by_name = true)
    WHERE NOT is_closed AND close_date IS NOT NULL
    GROUP BY 1
),
cap AS (
    SELECT
        CAST(DATE_TRUNC('month', CAST(mes AS DATE)) AS DATE) AS mes,
        SUM(horas_disponibles)                               AS capacidad_horas
    FROM read_parquet('s3://{bucket}/gold/replicon/costo_consultor_mensual/**/*.parquet',
                      hive_partitioning = true, union_by_name = true)
    GROUP BY 1
)
SELECT
    COALESCE(sf.mes, cap.mes)                                          AS mes,
    ROUND(sf.forecast_ponderado_usd, 2)                               AS forecast_ponderado_usd,
    cap.capacidad_horas,
    ROUND(sf.forecast_ponderado_usd / 150.0, 1)                       AS demanda_horas_estimada,
    ROUND(cap.capacidad_horas - sf.forecast_ponderado_usd / 150.0, 1) AS holgura_horas
FROM sf
FULL OUTER JOIN cap ON sf.mes = cap.mes
ORDER BY mes
