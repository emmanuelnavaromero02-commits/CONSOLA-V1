-- sap_successfactors_talent_simulation_inputs  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_operational_features"]
-- description: Variables agregadas internas para analisis WB-TALENTO. No expone PII ni nombres tecnicos al usuario final.

WITH features AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_operational_features/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
scored AS (
    SELECT
        *,
        CASE
            WHEN employee_count = 0 THEN 100.0
            ELSE LEAST(100.0, GREATEST(0.0,
                ((1.0 - COALESCE(confidence, 0.0)) * 40.0)
                + ((readiness_pending_count::DOUBLE / NULLIF(employee_count, 0)) * 25.0)
                + ((nine_box_blocked_count::DOUBLE / NULLIF(employee_count, 0)) * 15.0)
                + ((roles_without_requirements::DOUBLE / NULLIF(GREATEST(role_count, 1), 0)) * 10.0)
                + (LEAST(high_severity_signal_count, 10) * 1.0)
                + (LEAST(skill_gap_count, 10) * 1.0)
            ))
        END AS riesgo_base,
        LEAST(1.0, GREATEST(0.0, 1.0 - COALESCE(confidence, 0.0))) AS incertidumbre,
        CASE
            WHEN employee_count = 0 THEN 0
            WHEN readiness_status IN ('ready', 'benchmark_internal') THEN 3
            ELSE 0
        END AS scenario_count_calc
    FROM features
)
SELECT
    source_id,
    workspace_id,
    CURRENT_TIMESTAMP AS materialized_at,
    CASE
        WHEN employee_count = 0 THEN 'blocked'
        WHEN scenario_count_calc = 0 THEN 'blocked'
        WHEN readiness_status IN ('ready', 'benchmark_internal') THEN 'ready'
        WHEN readiness_status = 'partial' THEN 'partial'
        ELSE 'blocked'
    END AS input_status,
    CASE
        WHEN employee_count = 0 THEN 'En espera de datos'
        WHEN scenario_count_calc = 0 THEN 'Requiere historial adicional'
        WHEN readiness_status = 'benchmark_internal' THEN 'Analisis con referencia interna'
        WHEN readiness_status = 'partial' THEN 'Datos parciales'
        WHEN readiness_status = 'ready' THEN 'Analisis listo'
        ELSE 'En espera de datos'
    END AS user_status_label,
    employee_count,
    profiled_count,
    calculable_count,
    readiness_high,
    readiness_medium,
    readiness_low,
    readiness_pending_count,
    nine_box_classified_count,
    nine_box_blocked_count,
    roles_without_requirements AS roles_bloqueados,
    roles_without_requirements,
    open_signal_count AS senales_abiertas,
    open_signal_count,
    high_severity_signal_count,
    learning_blocked_count,
    recruiting_blocked_count,
    mobility_observed_count,
    skill_gap_count,
    skill_coverage_pct AS cobertura_skill,
    skill_coverage_pct,
    role_requirements_coverage_pct,
    nine_box_coverage_pct,
    confidence,
    readiness_status,
    source_mode,
    ROUND(riesgo_base, 2) AS riesgo_base,
    ROUND(incertidumbre, 4) AS incertidumbre,
    scenario_count_calc AS scenario_count,
    '{'
      || '"baseline_value":{"type":"fixed","value":' || CAST(ROUND(riesgo_base, 2) AS VARCHAR) || '},'
      || '"expected_delta":{"type":"triangular","low":' || CAST(ROUND(-1.0 * riesgo_base, 2) AS VARCHAR)
      || ',"mode":' || CAST(ROUND(-0.35 * riesgo_base, 2) AS VARCHAR)
      || ',"high":' || CAST(ROUND(0.10 * riesgo_base, 2) AS VARCHAR) || '},'
      || '"delay_days":{"type":"triangular","low":0,"mode":'
      || CAST(CASE WHEN high_severity_signal_count > 0 THEN 7 ELSE 3 END AS VARCHAR)
      || ',"high":' || CAST(CASE WHEN high_severity_signal_count > 0 THEN 21 ELSE 10 END AS VARCHAR) || '},'
      || '"cost_per_day":{"type":"fixed","value":1},'
      || '"probability_of_delay":{"type":"triangular","low":0.10,"mode":'
      || CAST(ROUND(LEAST(0.85, GREATEST(0.20, incertidumbre)), 2) AS VARCHAR)
      || ',"high":0.95}'
      || '}' AS input_variables_json,
    '[' ||
      '{"name":"riesgo_base","value":' || CAST(ROUND(riesgo_base, 2) AS VARCHAR) || '},' ||
      '{"name":"empleados_sin_readiness","value":' || CAST(readiness_pending_count AS VARCHAR) || '},' ||
      '{"name":"roles_bloqueados","value":' || CAST(roles_without_requirements AS VARCHAR) || '},' ||
      '{"name":"senales_abiertas","value":' || CAST(open_signal_count AS VARCHAR) || '},' ||
      '{"name":"cobertura_skill","value":' || CAST(ROUND(skill_coverage_pct, 2) AS VARCHAR) || '},' ||
      '{"name":"incertidumbre","value":' || CAST(ROUND(incertidumbre, 4) AS VARCHAR) || '}' ||
    ']' AS variables_json,
    '[' ||
      '{"source_dataset":"sap_successfactors_talent_operational_features","row_count":1},' ||
      '{"source_dataset":"sap_successfactors_talent_signals","row_count":' || CAST(open_signal_count AS VARCHAR) || '},' ||
      '{"source_dataset":"sap_successfactors_talent_readiness","row_count":' || CAST(calculable_count + readiness_pending_count AS VARCHAR) || '},' ||
      '{"source_dataset":"sap_successfactors_talent_9box","row_count":' || CAST(nine_box_classified_count + nine_box_blocked_count AS VARCHAR) || '}' ||
    ']' AS evidence_json,
    '[' ||
      '{"type":"gold_dataset","id":"sap_successfactors_talent_operational_features"},' ||
      '{"type":"wisdom_bit","id":"WB-TALENTO"}' ||
    ']' AS evidence_refs_json,
    '{'
      || '"basis":"Agregado WB-TALENTO sin PII",'
      || '"decision_mode":"recommendation_only",'
      || '"source_mode":"' || source_mode || '",'
      || '"readiness_status":"' || readiness_status || '",'
      || '"contract_version":"' || contract_version || '"'
      || '}' AS assumptions_json,
    '[' ||
      '{"name":"base","risk":' || CAST(ROUND(riesgo_base, 2) AS VARCHAR) || '},' ||
      '{"name":"conservative","risk":' || CAST(ROUND(LEAST(100.0, riesgo_base + (incertidumbre * 20.0)), 2) AS VARCHAR) || '},' ||
      '{"name":"improved","risk":' || CAST(ROUND(GREATEST(0.0, riesgo_base - 15.0), 2) AS VARCHAR) || '}' ||
    ']' AS escenarios,
    blockers,
    CASE
        WHEN scenario_count_calc = 0 THEN 'missing_simulation_inputs'
        WHEN readiness_status = 'partial' THEN 'missing_simulation_inputs'
        WHEN readiness_status = 'insufficient_data' THEN 'missing_simulation_inputs'
        ELSE NULL
    END AS blocked_reason,
    'talent_simulation_inputs.v2' AS analysis_contract_version,
    CURRENT_TIMESTAMP AS generated_at
FROM scored
