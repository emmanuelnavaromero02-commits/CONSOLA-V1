-- sap_successfactors_talent_benchmark_internal  (gold)  cartridge: sap_successfactors
-- sources: []
-- description: Referencia interna empaquetada y no revisada. Es un system default; no es sectorial, externa, aprendida ni aprobada por el tenant.

SELECT
    'WB-TALENTO' AS source_id,
    'talent_benchmark_internal.v1.unreviewed' AS benchmark_version,
    TRUE AS enabled,
    FALSE AS approved,
    NULL AS approved_by,
    NULL AS approved_at,
    'system_default' AS approval_source,
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
    '["benchmark_internal_unreviewed"]' AS blockers,
    'talent_benchmark_internal.v1' AS contract_version,
    CURRENT_TIMESTAMP AS materialized_at
