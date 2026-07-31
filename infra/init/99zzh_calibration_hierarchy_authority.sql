-- Persisted server-owned hierarchy and authoritative calibration provenance.

CREATE TABLE IF NOT EXISTS calibration_group_hierarchy (
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    child_group TEXT NOT NULL,
    parent_group TEXT NOT NULL,
    model_version TEXT NOT NULL,
    hierarchy_rule_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (workspace_id, child_group, model_version),
    CHECK (child_group <> parent_group)
);

ALTER TABLE calibration_group_hierarchy ENABLE ROW LEVEL SECURITY;
ALTER TABLE calibration_group_hierarchy FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS calibration_group_hierarchy_scope_rls
    ON calibration_group_hierarchy;
CREATE POLICY calibration_group_hierarchy_scope_rls
    ON calibration_group_hierarchy
    FOR SELECT TO omega_console
    USING (
        workspace_id::text = NULLIF(current_setting('app.workspace_id', true), '')
        AND tenant_id IS NOT DISTINCT FROM
            NULLIF(current_setting('app.tenant_id', true), '')::uuid
    );
REVOKE ALL ON calibration_group_hierarchy FROM omega_console;
GRANT SELECT ON calibration_group_hierarchy TO omega_console;

CREATE OR REPLACE FUNCTION public.enforce_calibration_observation_authority()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    authority RECORD;
    source_component TEXT;
    metric_component TEXT;
    raw_group TEXT;
    expected_group TEXT;
    parent_group_value TEXT;
BEGIN
    IF NEW.source_type <> 'prediction_outcome' THEN
        RAISE EXCEPTION 'calibration source is not authoritative'
            USING ERRCODE = '23514';
    END IF;
    SELECT outcome.actual_value, outcome.evaluation_status,
           outcome.evaluation_rule_version, outcome.evaluated_at,
           outcome.evaluated_by, signal.metric, signal.predicted_value,
           signal.prediction_horizon_days,
           signal.metadata->>'source_system' AS source_system
      INTO authority
      FROM public.prediction_outcomes outcome
      JOIN public.intelligence_signals signal
        ON signal.workspace_id = outcome.workspace_id
       AND signal.tenant_id IS NOT DISTINCT FROM outcome.tenant_id
       AND signal.signal_id = outcome.signal_id
     WHERE outcome.workspace_id = NEW.workspace_id
       AND outcome.tenant_id IS NOT DISTINCT FROM NEW.tenant_id
       AND outcome.id::text = NEW.source_id
       AND outcome.evaluation_status IN ('hit', 'miss')
       AND outcome.evaluation_rule_version IS NOT NULL
       AND outcome.evaluated_at IS NOT NULL
       AND outcome.evaluated_by = 'omega_outcome_evaluator.v1'
       AND outcome.metadata->>'observed' = 'true'
       AND jsonb_typeof(outcome.metadata->'evidence_refs') = 'array'
       AND jsonb_array_length(outcome.metadata->'evidence_refs') > 0
       AND signal.signal_subtype IN ('future_risk', 'future_opportunity')
       AND signal.prediction_horizon_days BETWEEN 1 AND 3650
       AND NULLIF(btrim(signal.metadata->>'source_system'), '') IS NOT NULL
       AND NULLIF(btrim(signal.metadata->>'source_dataset'), '') IS NOT NULL
       AND NULLIF(btrim(signal.metadata->>'evidence_pack_id'), '') IS NOT NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'authoritative calibration evidence unavailable'
            USING ERRCODE = '23514';
    END IF;

    source_component := regexp_replace(
        lower(btrim(authority.source_system)), '[^a-z0-9_-]', '_', 'g'
    );
    metric_component := regexp_replace(
        lower(btrim(authority.metric)), '[^a-z0-9_-]', '_', 'g'
    );
    source_component := COALESCE(
        NULLIF(regexp_replace(source_component, '^_+|_+$', '', 'g'), ''),
        'unknown'
    );
    metric_component := COALESCE(
        NULLIF(regexp_replace(metric_component, '^_+|_+$', '', 'g'), ''),
        'unknown'
    );
    raw_group := 'source_type:' || source_component || ':'
        || metric_component || ':v1';
    expected_group := CASE WHEN length(raw_group) <= 80 THEN raw_group ELSE
        'source_type:' || rtrim(
            left(source_component || '_' || metric_component || '_v1', 57), '_'
        ) || ':' || left(encode(public.digest(raw_group, 'sha256'), 'hex'), 10)
    END;
    raw_group := 'global:' || metric_component || ':v1';
    parent_group_value := CASE WHEN length(raw_group) <= 80 THEN raw_group ELSE
        'global:' || rtrim(left(metric_component || '_v1', 61), '_') || ':'
        || left(encode(public.digest(raw_group, 'sha256'), 'hex'), 10)
    END;

    IF NEW.calibration_group <> expected_group
       OR NEW.predicted_metric <> authority.metric
       OR NEW.predicted_value IS DISTINCT FROM authority.predicted_value
       OR NEW.predicted_probability IS NOT NULL
       OR NEW.predicted_interval <> '{}'::jsonb
       OR NEW.actual_value IS DISTINCT FROM authority.actual_value
       OR NEW.actual_status <> authority.evaluation_status
       OR NEW.observed_at <> authority.evaluated_at
       OR NEW.horizon_days <> authority.prediction_horizon_days
       OR NEW.model_version <> 'bayesian_calibration.v1'
    THEN
        RAISE EXCEPTION 'calibration observation does not match durable evidence'
            USING ERRCODE = '23514';
    END IF;

    INSERT INTO public.calibration_group_hierarchy (
        tenant_id, workspace_id, child_group, parent_group, model_version,
        hierarchy_rule_version
    )
    VALUES (
        NEW.tenant_id, NEW.workspace_id, expected_group, parent_group_value,
        NEW.model_version, 'metric_global_parent.v1'
    )
    ON CONFLICT (workspace_id, child_group, model_version) DO NOTHING;
    IF NOT EXISTS (
        SELECT 1 FROM public.calibration_group_hierarchy hierarchy
         WHERE hierarchy.workspace_id = NEW.workspace_id
           AND hierarchy.tenant_id IS NOT DISTINCT FROM NEW.tenant_id
           AND hierarchy.child_group = expected_group
           AND hierarchy.parent_group = parent_group_value
           AND hierarchy.model_version = NEW.model_version
    ) THEN
        RAISE EXCEPTION 'calibration hierarchy conflict'
            USING ERRCODE = '23514';
    END IF;

    NEW.provenance_status := 'verified';
    NEW.provenance_reason := 'durable_binary_evaluation';
    NEW.provenance_checked_at := NOW();
    NEW.authoritative_calibration_group := expected_group;
    NEW.evaluation_rule_version := authority.evaluation_rule_version;
    RETURN NEW;
END;
$$;

UPDATE calibration_states
   SET metrics = COALESCE(metrics, '{}'::jsonb) || jsonb_build_object(
           'complete', false,
           'provenance_complete', false,
           'binary_evaluation_complete', false,
           'reason', 'authoritative_recompute_required'
       ),
       updated_at = NOW()
 WHERE NOT COALESCE(metrics, '{}'::jsonb) @> jsonb_build_object(
       'complete', false,
       'provenance_complete', false,
       'binary_evaluation_complete', false,
       'reason', 'authoritative_recompute_required'
   );

INSERT INTO schema_migrations(filename, applied_at)
VALUES ('99zzh_calibration_hierarchy_authority.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
