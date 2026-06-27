-- sap_successfactors_talent_benchmark_internal  (gold)  cartridge: sap_successfactors
-- sources: ["config/sap_successfactors/talent_benchmark_internal"]
-- description: Contrato interno versionado para clasificacion Talent. Disabled por defecto; no clasifica sin aprobacion explicita.

SELECT
    'WB-TALENTO' AS source_id,
    'talent_benchmark_internal.v1.disabled' AS benchmark_version,
    FALSE AS enabled,
    FALSE AS approved,
    NULL AS approved_by,
    NULL AS approved_at,
    'disabled_by_default' AS approval_source,
    0.80 AS minimum_profile_coverage,
    80.0 AS readiness_high_threshold,
    60.0 AS readiness_medium_threshold,
    4.0 AS performance_high_threshold,
    3.0 AS performance_medium_threshold,
    4.0 AS potential_high_threshold,
    3.0 AS potential_medium_threshold,
    0.60 AS competency_weight,
    0.25 AS role_coverage_weight,
    0.15 AS tenure_weight,
    '["benchmark_internal_not_configured"]' AS blockers,
    'talent_benchmark_internal.v1' AS contract_version,
    CURRENT_TIMESTAMP AS materialized_at
