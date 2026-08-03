-- Derive a scoped parent prior from durable outcomes of persisted child groups.
CREATE OR REPLACE FUNCTION public.authoritative_calibration_parent_evidence(
    p_workspace_id UUID,
    p_tenant_id UUID,
    p_child_group TEXT,
    p_model_version TEXT
)
RETURNS TABLE(
    parent_group TEXT,
    parent_sample_count INTEGER,
    parent_posterior_mean NUMERIC
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
    WITH requested_parent AS (
        SELECT hierarchy.parent_group
          FROM public.calibration_group_hierarchy hierarchy
         WHERE hierarchy.workspace_id = p_workspace_id
           AND hierarchy.tenant_id IS NOT DISTINCT FROM p_tenant_id
           AND hierarchy.child_group = p_child_group
           AND hierarchy.model_version = p_model_version
    ),
    trusted AS (
        SELECT DISTINCT ON (outcome.id)
               outcome.id, outcome.evaluation_status
          FROM requested_parent requested
          JOIN public.calibration_group_hierarchy hierarchy
            ON hierarchy.workspace_id = p_workspace_id
           AND hierarchy.tenant_id IS NOT DISTINCT FROM p_tenant_id
           AND hierarchy.parent_group = requested.parent_group
           AND hierarchy.model_version = p_model_version
          JOIN public.calibration_observations observation
            ON observation.workspace_id = hierarchy.workspace_id
           AND observation.tenant_id IS NOT DISTINCT FROM hierarchy.tenant_id
           AND observation.calibration_group = hierarchy.child_group
           AND observation.model_version = hierarchy.model_version
          JOIN public.prediction_outcomes outcome
            ON outcome.workspace_id = observation.workspace_id
           AND outcome.tenant_id IS NOT DISTINCT FROM observation.tenant_id
           AND outcome.id::text = observation.source_id
          JOIN public.intelligence_signals signal
            ON signal.workspace_id = outcome.workspace_id
           AND signal.tenant_id IS NOT DISTINCT FROM outcome.tenant_id
           AND signal.signal_id = outcome.signal_id
         WHERE observation.source_type = 'prediction_outcome'
           AND observation.provenance_status = 'verified'
           AND observation.provenance_reason = 'durable_binary_evaluation'
           AND observation.authoritative_calibration_group =
               observation.calibration_group
           AND outcome.evaluation_status IN ('hit', 'miss')
           AND outcome.evaluation_rule_version =
               observation.evaluation_rule_version
           AND outcome.evaluated_at = observation.observed_at
           AND outcome.evaluated_by = 'omega_outcome_evaluator.v1'
           AND outcome.metadata->>'observed' = 'true'
           AND outcome.actual_value IS NOT DISTINCT FROM
               observation.actual_value
           AND signal.metric = observation.predicted_metric
           AND signal.predicted_value IS NOT DISTINCT FROM
               observation.predicted_value
           AND signal.prediction_horizon_days = observation.horizon_days
           AND signal.signal_subtype IN ('future_risk', 'future_opportunity')
         ORDER BY outcome.id, observation.id
    )
    SELECT requested.parent_group,
           count(trusted.id)::integer,
           (
               1 + count(trusted.id) FILTER (
                   WHERE trusted.evaluation_status = 'hit'
               )
           )::numeric / (2 + count(trusted.id))::numeric
      FROM requested_parent requested
      LEFT JOIN trusted ON true
     GROUP BY requested.parent_group;
$$;

REVOKE ALL ON FUNCTION public.authoritative_calibration_parent_evidence(
    UUID, UUID, TEXT, TEXT
) FROM PUBLIC;

INSERT INTO schema_migrations(filename, applied_at)
VALUES ('99zzjj_calibration_parent_evidence.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
