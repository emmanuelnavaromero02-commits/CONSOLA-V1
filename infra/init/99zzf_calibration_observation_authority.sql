-- Server-owned provenance for newly inserted calibration observations.

CREATE OR REPLACE FUNCTION enforce_calibration_observation_authority()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    authority RECORD;
    source_component TEXT;
    metric_component TEXT;
    raw_group TEXT;
    group_body TEXT;
    expected_group TEXT;
BEGIN
    IF NEW.source_type = 'manual_fixture' THEN
        RAISE EXCEPTION 'manual fixture persistence is test-harness only'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.source_type <> 'prediction_outcome' THEN
        RAISE EXCEPTION 'calibration source is not authoritative'
            USING ERRCODE = '23514';
    END IF;
    SELECT outcome.actual_value,
           outcome.evaluation_status,
           outcome.evaluation_rule_version,
           outcome.created_at,
           signal.metric,
           signal.predicted_value,
           signal.prediction_horizon_days,
           signal.metadata->>'source_system' AS source_system
      INTO authority
      FROM prediction_outcomes outcome
      JOIN intelligence_signals signal
        ON signal.workspace_id = outcome.workspace_id
       AND signal.tenant_id IS NOT DISTINCT FROM outcome.tenant_id
       AND signal.signal_id = outcome.signal_id
     WHERE outcome.workspace_id = NEW.workspace_id
       AND outcome.tenant_id IS NOT DISTINCT FROM NEW.tenant_id
       AND outcome.id::text = NEW.source_id
       AND outcome.evaluation_status IN ('hit', 'miss')
       AND outcome.evaluation_rule_version IS NOT NULL
       AND outcome.evaluated_at IS NOT NULL
       AND outcome.evaluated_by IS NOT NULL
       AND signal.signal_subtype = 'observed'
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
    source_component := regexp_replace(source_component, '^_+|_+$', '', 'g');
    metric_component := regexp_replace(
        lower(btrim(authority.metric)), '[^a-z0-9_-]', '_', 'g'
    );
    metric_component := regexp_replace(metric_component, '^_+|_+$', '', 'g');
    source_component := COALESCE(NULLIF(source_component, ''), 'unknown');
    metric_component := COALESCE(NULLIF(metric_component, ''), 'unknown');
    raw_group := 'source_type:' || source_component || ':'
        || metric_component || ':v1';
    IF length(raw_group) <= 80 THEN
        expected_group := raw_group;
    ELSE
        group_body := source_component || '_' || metric_component || '_v1';
        expected_group := 'source_type:'
            || rtrim(left(group_body, 57), '_') || ':'
            || left(encode(digest(raw_group, 'sha256'), 'hex'), 10);
    END IF;
    IF NEW.calibration_group <> expected_group
       OR NEW.predicted_metric <> authority.metric
       OR NEW.predicted_value IS DISTINCT FROM authority.predicted_value
       OR NEW.predicted_probability IS NOT NULL
       OR NEW.predicted_interval <> '{}'::jsonb
       OR NEW.actual_value IS DISTINCT FROM authority.actual_value
       OR NEW.actual_status <> authority.evaluation_status
       OR NEW.observed_at <> authority.created_at
       OR NEW.horizon_days <> authority.prediction_horizon_days
       OR NEW.model_version <> 'bayesian_calibration.v1'
    THEN
        RAISE EXCEPTION 'calibration observation does not match durable evidence'
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

DROP TRIGGER IF EXISTS calibration_observation_authority
    ON calibration_observations;
CREATE TRIGGER calibration_observation_authority
BEFORE INSERT ON calibration_observations
FOR EACH ROW
EXECUTE FUNCTION enforce_calibration_observation_authority();

REVOKE UPDATE, DELETE ON calibration_observations FROM omega_console;
REVOKE INSERT ON calibration_observations FROM omega_console;
GRANT SELECT ON calibration_observations TO omega_console;
GRANT INSERT (
    observation_id, idempotency_key, evidence_digest,
    tenant_id, workspace_id, source_type, source_id,
    predicted_metric, predicted_value, predicted_interval,
    predicted_probability, actual_value, actual_status, observed_at,
    horizon_days, model_version, calibration_group, prior, posterior,
    metrics, evidence_refs, explanation, reproducibility_hash, created_by
) ON calibration_observations TO omega_console;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zzf_calibration_observation_authority.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
