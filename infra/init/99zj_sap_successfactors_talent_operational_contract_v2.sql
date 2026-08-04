-- WB-TALENTO operational contract v2 (part 1; continued by 99zja_*).
WITH target_workspace AS (
    SELECT id, tenant_id
      FROM workspaces
     ORDER BY created_at ASC
     LIMIT 1
),
ranked_unscoped AS (
    SELECT d.ctid,
           d.name,
           ROW_NUMBER() OVER (PARTITION BY d.name ORDER BY d.updated_at DESC NULLS LAST, d.ctid) AS rn
      FROM datasets d
     WHERE d.cartridge = 'sap_successfactors'
       AND d.workspace_id IS NULL
       AND d.name IN (
            'sap_successfactors_talent_benchmark_internal',
            'sap_successfactors_talent_operational_features',
            'sap_successfactors_talent_simulation_inputs'
       )
),
promoted AS (
    UPDATE datasets d
       SET workspace_id = tw.id,
           tenant_id = COALESCE(d.tenant_id, tw.tenant_id),
           scope_status = 'scoped',
           updated_at = NOW()
      FROM ranked_unscoped ru
      JOIN target_workspace tw ON TRUE
     WHERE d.ctid = ru.ctid
       AND ru.rn = 1
       AND NOT EXISTS (
            SELECT 1
              FROM datasets existing
             WHERE existing.workspace_id = tw.id
               AND existing.name = d.name
       )
     RETURNING d.name
)
DELETE FROM datasets d
 USING target_workspace tw
 WHERE d.cartridge = 'sap_successfactors'
   AND d.workspace_id IS NULL
   AND d.name IN (
        'sap_successfactors_talent_benchmark_internal',
        'sap_successfactors_talent_operational_features',
        'sap_successfactors_talent_simulation_inputs'
   );

WITH target_workspace AS (
    SELECT id, tenant_id
      FROM workspaces
     ORDER BY created_at ASC
     LIMIT 1
)
INSERT INTO datasets (name, layer, cartridge, sources, sql_def, description, column_mapping, schedule, updated_at, workspace_id, tenant_id, scope_status)
SELECT seed.name,
       seed.layer,
       seed.cartridge,
       seed.sources,
       seed.sql_def,
       seed.description,
       seed.column_mapping,
       seed.schedule,
       NOW(),
       tw.id,
       tw.tenant_id,
       'scoped'
  FROM target_workspace tw
 CROSS JOIN (
    VALUES
    ('sap_successfactors_talent_benchmark_internal', 'gold', 'sap_successfactors', '[]'::jsonb, 'SELECT 1 AS placeholder', 'Benchmark interno Talent no revisado.', '{}'::jsonb, NULL),
    ('sap_successfactors_talent_operational_features', 'gold', 'sap_successfactors', '[]'::jsonb, 'SELECT 1 AS placeholder', 'Feature pack agregado WB-TALENTO.', '{}'::jsonb, NULL),
    ('sap_successfactors_talent_simulation_inputs', 'gold', 'sap_successfactors', '[]'::jsonb, 'SELECT 1 AS placeholder', 'Inputs agregados WB-TALENTO.', '{}'::jsonb, NULL)
 ) AS seed(name, layer, cartridge, sources, sql_def, description, column_mapping, schedule)
 WHERE NOT EXISTS (
    SELECT 1
      FROM datasets existing
     WHERE existing.workspace_id = tw.id
       AND existing.name = seed.name
 )
ON CONFLICT (workspace_id, name) DO NOTHING;

UPDATE datasets
   SET sources = '["config/sap_successfactors/talent_benchmark_internal"]'::jsonb,
       sql_def = $sql$
-- sap_successfactors_talent_benchmark_internal  (gold)  cartridge: sap_successfactors
-- sources: ["config/sap_successfactors/talent_benchmark_internal"]
-- description: Referencia interna no revisada; nunca se activa sin aprobación durable registrada por servidor.

SELECT
    'WB-TALENTO' AS source_id,
    'talent_benchmark_internal.v1.unreviewed' AS benchmark_version,
    TRUE AS enabled,
    FALSE AS approved,
    NULL AS approved_by,
    NULL AS approved_at,
    'system_default' AS approval_source,
    NULL AS approval_actor_source,
    FALSE AS approval_recorded_by_server,
    NULL AS approval_evidence_ref, NULL AS approval_authorization_ref,
    FALSE AS approval_authorization_verified, 'unreviewed' AS approval_status,
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
$sql$,
       description = 'Benchmark interno no revisado para WB-TALENTO.',
       updated_at = NOW()
 WHERE name = 'sap_successfactors_talent_benchmark_internal'
   AND cartridge = 'sap_successfactors';
