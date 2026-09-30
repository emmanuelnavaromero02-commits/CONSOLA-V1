-- sap_successfactors_talent_attrition_exposure  (gold)  cartridge: sap_successfactors
-- sources: ["silver/sap_successfactors/sap_successfactors_emppaycomprecurring_latest", "silver/sap_successfactors/sap_successfactors_emppaycompnonrecurring_latest", "gold/sap_successfactors/sap_successfactors_talent_retention_risk"]
-- description: Exposicion monetaria anualizada agregada por banda de riesgo y moneda en todo el workspace (solo grupos de 5+ personas donde nadie supera la mitad del total; el total se redondea a una unidad no menor que el orden de magnitud del mayor importe individual ni que 2 cifras significativas y el promedio se deriva del total redondeado; sin llaves de empleado). La publica el agregador Python de refinement: descifra importes solo en memoria, anualiza los pagos recurrentes vigentes, excluye importes cifrados repetidos y cuenta sin sumar los no recurrentes de los ultimos 365 dias; sin la llave de cifrado publica cero filas.

SELECT
    CAST(NULL AS VARCHAR) AS risk_band,
    CAST(NULL AS VARCHAR) AS currency,
    CAST(NULL AS BIGINT) AS headcount,
    CAST(NULL AS DECIMAL(38, 2)) AS annualized_comp_total,
    CAST(NULL AS DECIMAL(38, 2)) AS annualized_comp_avg,
    CAST(NULL AS BIGINT) AS excluded_undecryptable,
    CAST(NULL AS BIGINT) AS excluded_unknown_frequency,
    CAST(NULL AS BIGINT) AS excluded_invalid_amount,
    CAST(NULL AS BIGINT) AS excluded_non_recurring_365d,
    'aggregate_min5_dominance50_unitmax' AS privacy_rule,
    'talent_attrition_exposure.v4' AS contract_version,
    CURRENT_TIMESTAMP AS generated_at
FROM (SELECT 'managed_by_sap_successfactors_exposure_materializer' AS note) AS managed
WHERE FALSE
