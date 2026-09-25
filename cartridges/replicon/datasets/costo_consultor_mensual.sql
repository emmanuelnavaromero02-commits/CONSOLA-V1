-- costo_consultor_mensual  (gold)  cartridge: replicon
-- sources: ["raw/replicon/TimeEntry", "silver/replicon/replicon_timeentry_latest", "silver/replicon/replicon_user_latest"]
-- description: Costo mensual por consultor: ejecutado (horas×rate) y hundido (horas disponibles - ejecutadas)×rate

WITH te AS (
    SELECT userid, username,
        
        DATE_TRUNC('month', CAST(entrydate AS DATE)) AS mes,
        SUM(durationhours) AS horas_ejecutadas
    FROM read_parquet('s3://{bucket}/silver/replicon/replicon_timeentry_latest/data.parquet')
    GROUP BY userid, username, DATE_TRUNC('year', CAST(entrydate AS DATE)),DATE_TRUNC('month', CAST(entrydate AS DATE))
),
u AS (
    SELECT userid,
        CONCAT(firstname, ' ', lastname) AS nombre_completo,
        departmentname AS departamento,
        currenthourlycostamount AS rate_costo
    FROM read_parquet('s3://{bucket}/silver/replicon/replicon_user_latest/data.parquet')
),
horas_mes AS (
    SELECT mes,
        ROUND(EXTRACT(DAY FROM (mes + INTERVAL '1 month' - INTERVAL '1 day'))::INT * 5.0 / 7.0 * 8, 2) AS horas_disponibles
    FROM (SELECT DISTINCT mes FROM te)
)
SELECT
   
   te.mes,
    te.userid,
    u.nombre_completo,
    te.username,
    u.departamento,
    u.rate_costo,
    hm.horas_disponibles,
    ROUND(te.horas_ejecutadas, 2)                                                     AS horas_ejecutadas,
    GREATEST(ROUND(hm.horas_disponibles - te.horas_ejecutadas, 2), 0)                AS horas_hundidas,
    ROUND(te.horas_ejecutadas * u.rate_costo, 2)                                     AS costo_ejecutado,
    ROUND(GREATEST(hm.horas_disponibles - te.horas_ejecutadas, 0) * u.rate_costo, 2) AS costo_hundido,
    ROUND(hm.horas_disponibles * u.rate_costo, 2)                                    AS costo_potencial_mes
FROM te
JOIN u  ON u.userid = te.userid
JOIN horas_mes hm ON hm.mes = te.mes
ORDER BY te.mes DESC, u.nombre_completo
