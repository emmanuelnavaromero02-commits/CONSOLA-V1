-- Register the per-position succession coverage Gold dataset and backfill Position once so optional fields reach every row.

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
    ('sap_successfactors_talent_succession_coverage', 'gold', 'sap_successfactors', '["silver/sap_successfactors/sap_successfactors_position_latest", "silver/sap_successfactors/sap_successfactors_successionnomination_latest"]'::jsonb, 'SELECT 1 AS placeholder', 'Cobertura de sucesion por posicion activa con la criticidad de los datos extraidos.', '{}'::jsonb, NULL)
 ) AS seed(name, layer, cartridge, sources, sql_def, description, column_mapping, schedule)
 WHERE NOT EXISTS (
    SELECT 1
      FROM datasets existing
     WHERE existing.workspace_id = tw.id
       AND existing.name = seed.name
 )
ON CONFLICT (workspace_id, name) DO NOTHING;

UPDATE datasets
   SET sources = '["silver/sap_successfactors/sap_successfactors_position_latest", "silver/sap_successfactors/sap_successfactors_successionnomination_latest"]'::jsonb,
       sql_def = $sql$
-- sap_successfactors_talent_succession_coverage  (gold)  cartridge: sap_successfactors
-- sources: ["silver/sap_successfactors/sap_successfactors_position_latest", "silver/sap_successfactors/sap_successfactors_successionnomination_latest"]
-- description: Cobertura de sucesion por posicion activa con la criticidad de los datos extraidos de Position (sin llaves de persona); critical_without_nominee_total es NULL mientras la criticidad, las nominaciones o su cruce no permitan decidir la cobertura, y nunca es cero si alguna posicion activa no tiene criticidad; si todas las posiciones estan inactivas se publica una sola fila sin posicion (position_id NULL, positions_total = 0).
-- is_critical mapping (lowercase, no accents, spaces/_/- collapsed): critical, critica, critico, mission critical, business critical, very critical, muy critica, muy critico, high, very high, alta, alto, muy alta, muy alto, key, key position, clave, posicion clave, puesto clave => TRUE; not critical, notcritical, non critical, noncritical, no critica, no critico, low, very low, baja, bajo, muy baja, muy bajo, medium, media, medio, normal, standard, estandar => FALSE; any other non-NULL value (numeric codes included) => NULL, counted in criticality_unrecognized_count.

WITH nominations_input AS (
    SELECT TRUE AS nominations_available
),
position_columns AS (
    SELECT
        CAST(NULL AS VARCHAR) AS criticality,
        CAST(NULL AS BOOLEAN) AS is_vacant,
        CAST(NULL AS VARCHAR) AS effective_status
    LIMIT 0
),
position_source AS (
    SELECT * FROM position_columns
    UNION ALL BY NAME
    SELECT *
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_position_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
all_positions AS (
    SELECT
        tenant_id,
        workspace_id,
        NULLIF(TRIM(CAST(position_id AS VARCHAR)), '') AS position_id,
        position_name,
        department,
        NULLIF(TRIM(CAST(criticality AS VARCHAR)), '') AS criticality,
        is_vacant,
        UPPER(NULLIF(TRIM(CAST(effective_status AS VARCHAR)), '')) AS effective_status
    FROM position_source
),
classified AS (
    SELECT
        *,
        CASE
            WHEN criticality_key IN (
                'critical', 'critica', 'critico', 'mission critical', 'business critical',
                'very critical', 'muy critica', 'muy critico',
                'high', 'very high', 'alta', 'alto', 'muy alta', 'muy alto',
                'key', 'key position', 'clave', 'posicion clave', 'puesto clave'
            ) THEN TRUE
            WHEN criticality_key IN (
                'not critical', 'notcritical', 'non critical', 'noncritical', 'no critica', 'no critico',
                'low', 'very low', 'baja', 'bajo', 'muy baja', 'muy bajo',
                'medium', 'media', 'medio', 'normal', 'standard', 'estandar'
            ) THEN FALSE
        END AS is_critical
    FROM (
        SELECT
            *,
            TRIM(REGEXP_REPLACE(STRIP_ACCENTS(LOWER(criticality)), '[[:space:]_-]+', ' ', 'g')) AS criticality_key
        FROM all_positions
        WHERE position_id IS NOT NULL
          AND COALESCE(effective_status, '') NOT IN ('I', 'INACTIVE')
    )
),
nomination_source AS (
    SELECT
        tenant_id,
        workspace_id,
        user_id,
        NULLIF(TRIM(CAST(target_position AS VARCHAR)), '') AS target_position,
        NULLIF(TRIM(CAST(readiness AS VARCHAR)), '') AS readiness,
        TRIM(REGEXP_REPLACE(STRIP_ACCENTS(LOWER(TRIM(CAST(nomination_status AS VARCHAR)))), '[[:space:]_-]+', ' ', 'g')) AS status_key,
        TRIM(REGEXP_REPLACE(STRIP_ACCENTS(LOWER(TRIM(CAST(readiness AS VARCHAR)))), '[[:space:]_-]+', ' ', 'g')) AS readiness_key
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_successionnomination_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
nomination_state AS (
    SELECT
        *,
        CASE
            WHEN status_key IN (
                'active', 'approved', 'accepted', 'confirmed', 'nominated',
                'activo', 'activa', 'aprobado', 'aprobada', 'aceptado', 'aceptada',
                'confirmado', 'confirmada', 'nominado', 'nominada'
            ) THEN 'active'
            WHEN status_key IN (
                'inactive', 'rejected', 'declined', 'cancelled', 'canceled', 'withdrawn',
                'removed', 'deleted', 'expired', 'closed', 'obsolete',
                'inactivo', 'inactiva', 'rechazado', 'rechazada', 'cancelado', 'cancelada',
                'retirado', 'retirada', 'eliminado', 'eliminada', 'vencido', 'vencida',
                'cerrado', 'cerrada'
            ) THEN 'inactive'
            ELSE 'unknown'
        END AS nomination_state,
        CASE
            WHEN REGEXP_FULL_MATCH(
                readiness_key,
                '(ready )?now|ready immediately|immediate(ly)?|(listo|lista) (ahora|ya)|inmediat[oa]|ahora'
            ) THEN 1
            WHEN REGEXP_FULL_MATCH(
                readiness_key,
                '((ready|listo|lista) (in|en) )?(1 (to |a )?2 (years?|yrs?|anos?)|12 (to |a )?24 (months?|meses))'
            ) THEN 2
            WHEN REGEXP_FULL_MATCH(
                readiness_key,
                '((ready|listo|lista) (in|en) )?((2 (to |a )?3|3 (to |a )?5) (years?|yrs?|anos?)|(24 (to |a )?36|36 (to |a )?60) (months?|meses))'
            ) THEN 3
        END AS readiness_rank
    FROM nomination_source
    WHERE target_position IS NOT NULL
),
coverage AS (
    SELECT
        tenant_id,
        workspace_id,
        target_position,
        COUNT(*) FILTER (WHERE nomination_state = 'active') AS active_nominations,
        COUNT(*) FILTER (WHERE nomination_state = 'unknown') AS unknown_nominations,
        COUNT(DISTINCT user_id) FILTER (WHERE nomination_state = 'active') AS nominee_count,
        ARG_MIN(readiness, CAST(readiness_rank AS VARCHAR) || readiness) FILTER (
            WHERE nomination_state = 'active' AND readiness_rank IS NOT NULL
        ) AS readiness_best
    FROM nomination_state
    GROUP BY tenant_id, workspace_id, target_position
),
nomination_scope AS (
    SELECT
        n.tenant_id,
        n.workspace_id,
        COUNT(*) AS nominations_total,
        COUNT(*) FILTER (WHERE known.position_id IS NOT NULL) AS nominations_matched,
        COUNT(*) FILTER (
            WHERE known.position_id IS NULL AND n.nomination_state <> 'inactive'
        ) AS nominations_unmatched_open
    FROM nomination_state n
    LEFT JOIN (
        SELECT DISTINCT tenant_id, workspace_id, position_id
        FROM all_positions
        WHERE position_id IS NOT NULL
    ) known
        ON known.position_id = n.target_position
       AND known.tenant_id IS NOT DISTINCT FROM n.tenant_id
       AND known.workspace_id IS NOT DISTINCT FROM n.workspace_id
    GROUP BY n.tenant_id, n.workspace_id
),
scope_inputs AS (
    SELECT
        s.tenant_id,
        s.workspace_id,
        i.nominations_available,
        COALESCE(ns.nominations_total, 0) AS nominations_total,
        COALESCE(ns.nominations_matched, 0) AS nominations_matched,
        COALESCE(ns.nominations_unmatched_open, 0) AS nominations_unmatched_open
    FROM (SELECT DISTINCT tenant_id, workspace_id FROM all_positions WHERE position_id IS NOT NULL) s
    CROSS JOIN nominations_input i
    LEFT JOIN nomination_scope ns
        ON ns.tenant_id IS NOT DISTINCT FROM s.tenant_id
       AND ns.workspace_id IS NOT DISTINCT FROM s.workspace_id
),
position_coverage AS (
    SELECT
        c.tenant_id,
        c.workspace_id,
        c.position_id,
        c.position_name,
        c.department,
        c.criticality,
        c.is_critical,
        c.is_vacant,
        COALESCE(cov.nominee_count, 0) AS nominee_count,
        CASE
            WHEN COALESCE(cov.active_nominations, 0) > 0 THEN TRUE
            WHEN NOT si.nominations_available
              OR si.nominations_total = 0
              OR si.nominations_matched = 0
              OR si.nominations_unmatched_open > 0
              OR COALESCE(cov.unknown_nominations, 0) > 0 THEN NULL
            ELSE FALSE
        END AS has_active_nominee,
        cov.readiness_best
    FROM classified c
    JOIN scope_inputs si
        ON si.tenant_id IS NOT DISTINCT FROM c.tenant_id
       AND si.workspace_id IS NOT DISTINCT FROM c.workspace_id
    LEFT JOIN coverage cov
        ON cov.target_position = c.position_id
       AND cov.tenant_id IS NOT DISTINCT FROM c.tenant_id
       AND cov.workspace_id IS NOT DISTINCT FROM c.workspace_id
),
position_flags AS (
    SELECT
        tenant_id,
        workspace_id,
        COUNT(*) AS positions_total,
        BOOL_OR(criticality IS NOT NULL) AS criticality_available,
        COUNT(*) FILTER (WHERE criticality IS NOT NULL AND is_critical IS NULL) AS criticality_unrecognized_count,
        COUNT(*) FILTER (WHERE criticality IS NULL) AS criticality_missing_count,
        COUNT(*) FILTER (WHERE is_critical IS TRUE) AS critical_total,
        COUNT(*) FILTER (WHERE is_critical IS TRUE AND has_active_nominee IS FALSE) AS critical_without_nominee_count,
        COUNT(*) FILTER (WHERE is_critical IS TRUE AND has_active_nominee IS NULL) AS critical_coverage_unknown_count
    FROM position_coverage
    GROUP BY tenant_id, workspace_id
),
inactive_positions AS (
    SELECT tenant_id, workspace_id, COUNT(DISTINCT position_id) AS positions_inactive_count
    FROM all_positions
    WHERE position_id IS NOT NULL
      AND effective_status IN ('I', 'INACTIVE')
    GROUP BY tenant_id, workspace_id
),
dataset_flags AS (
    SELECT
        si.tenant_id,
        si.workspace_id,
        COALESCE(pf.positions_total, 0) AS positions_total,
        COALESCE(ip.positions_inactive_count, 0) AS positions_inactive_count,
        COALESCE(pf.criticality_available, FALSE) AS criticality_available,
        COALESCE(pf.criticality_unrecognized_count, 0) AS criticality_unrecognized_count,
        COALESCE(pf.criticality_missing_count, 0) AS criticality_missing_count,
        COALESCE(pf.critical_total, 0) AS critical_total,
        CASE
            WHEN COALESCE(pf.positions_total, 0) > 0
             AND pf.criticality_available
             AND pf.criticality_unrecognized_count = 0
             AND NOT (pf.critical_total = 0 AND pf.criticality_missing_count > 0)
             AND NOT (pf.critical_without_nominee_count = 0 AND pf.criticality_missing_count > 0)
             AND si.nominations_available
             AND si.nominations_total > 0
             AND si.nominations_matched > 0
             AND pf.critical_coverage_unknown_count = 0
            THEN pf.critical_without_nominee_count
        END AS critical_without_nominee_total,
        COALESCE(pf.critical_coverage_unknown_count, 0) AS critical_coverage_unknown_count,
        si.nominations_available,
        si.nominations_total,
        si.nominations_matched,
        si.nominations_unmatched_open
    FROM scope_inputs si
    LEFT JOIN position_flags pf
        ON pf.tenant_id IS NOT DISTINCT FROM si.tenant_id
       AND pf.workspace_id IS NOT DISTINCT FROM si.workspace_id
    LEFT JOIN inactive_positions ip
        ON ip.tenant_id IS NOT DISTINCT FROM si.tenant_id
       AND ip.workspace_id IS NOT DISTINCT FROM si.workspace_id
),
output_rows AS (
    SELECT
        tenant_id,
        workspace_id,
        position_id,
        position_name,
        department,
        criticality,
        is_critical,
        is_vacant,
        has_active_nominee,
        nominee_count,
        readiness_best
    FROM position_coverage
    UNION ALL BY NAME
    SELECT tenant_id, workspace_id
    FROM dataset_flags
    WHERE positions_total = 0
)
SELECT
    o.tenant_id,
    o.workspace_id,
    o.position_id,
    o.position_name,
    o.department,
    o.criticality,
    o.is_critical,
    o.is_vacant,
    o.has_active_nominee,
    o.nominee_count,
    o.readiness_best,
    f.positions_total,
    f.positions_inactive_count,
    f.criticality_available,
    f.criticality_unrecognized_count,
    f.criticality_missing_count,
    f.critical_total,
    f.critical_without_nominee_total,
    f.critical_coverage_unknown_count,
    f.nominations_available,
    f.nominations_total,
    f.nominations_matched,
    f.nominations_unmatched_open,
    'talent_succession_coverage.v1' AS contract_version,
    CURRENT_TIMESTAMP AS generated_at
FROM output_rows o
JOIN dataset_flags f
    ON f.tenant_id IS NOT DISTINCT FROM o.tenant_id
   AND f.workspace_id IS NOT DISTINCT FROM o.workspace_id
ORDER BY
    (o.is_critical IS TRUE AND o.has_active_nominee IS FALSE) DESC,
    o.position_name,
    o.position_id
$sql$,
       description = 'Cobertura de sucesion por posicion activa con la criticidad de los datos extraidos de Position (sin llaves de persona).',
       updated_at = NOW()
 WHERE name = 'sap_successfactors_talent_succession_coverage'
   AND cartridge = 'sap_successfactors';

UPDATE entity_watermarks
   SET last_watermark_value = NULL
 WHERE cartridge_id = 'sap_successfactors'
   AND entity_name = 'Position';

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zzzzzzc_talent_succession_coverage.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
