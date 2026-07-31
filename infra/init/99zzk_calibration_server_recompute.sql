-- Recompute calibration state only from scoped, durable server-owned outcomes.
CREATE OR REPLACE FUNCTION public.upsert_calibration_state(p_payload JSONB)
RETURNS SETOF public.calibration_states
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    scope_workspace UUID;
    scope_tenant UUID;
    group_value TEXT;
    version_value TEXT;
    requested_complete BOOLEAN;
    trusted_total INTEGER;
    hit_total INTEGER;
    miss_total INTEGER;
    mae_value NUMERIC;
    rmse_value NUMERIC;
    last_observed TIMESTAMPTZ;
    evidence_identity TEXT;
    parent_group_value TEXT;
    parent_sample_total INTEGER;
    parent_mean NUMERIC;
    prior_strength INTEGER;
    prior_alpha NUMERIC := 1;
    prior_beta NUMERIC := 1;
    posterior_alpha NUMERIC;
    posterior_beta NUMERIC;
    posterior_mean NUMERIC;
    posterior_variance NUMERIC;
    posterior_margin NUMERIC;
    confidence_value NUMERIC;
    prior_value JSONB;
    posterior_value JSONB;
    metrics_value JSONB;
    state_identity TEXT;
    state_hash TEXT;
BEGIN
    scope_workspace := NULLIF(
        current_setting('app.workspace_id', true), ''
    )::uuid;
    scope_tenant := NULLIF(current_setting('app.tenant_id', true), '')::uuid;
    group_value := NULLIF(btrim(p_payload->>'calibration_group'), '');
    version_value := NULLIF(btrim(p_payload->>'model_version'), '');
    requested_complete := COALESCE(
        (p_payload #>> '{metrics,complete}')::boolean, false
    );
    IF scope_workspace IS NULL THEN
        RAISE EXCEPTION 'calibration scope denied' USING ERRCODE = '42501';
    END IF;
    IF jsonb_typeof(p_payload) <> 'object'
       OR group_value IS NULL
       OR length(group_value) > 80
       OR version_value <> 'bayesian_calibration.v1'
    THEN
        RAISE EXCEPTION 'calibration state payload invalid'
            USING ERRCODE = '22023';
    END IF;
    SELECT count(*)::integer,
           count(*) FILTER (WHERE outcome.evaluation_status = 'hit')::integer,
           count(*) FILTER (WHERE outcome.evaluation_status = 'miss')::integer,
           round(avg(abs(
               outcome.actual_value - signal.predicted_value
           )), 6),
           round(sqrt(avg(power(
               outcome.actual_value - signal.predicted_value, 2
           ))), 6),
           max(outcome.evaluated_at),
           string_agg(
               observation.id::text || ':' || observation.evidence_digest
               || ':' || outcome.id::text || ':'
               || outcome.evaluation_rule_version || ':'
               || outcome.evaluation_status,
               '|' ORDER BY observation.id
           )
      INTO trusted_total, hit_total, miss_total, mae_value, rmse_value,
           last_observed, evidence_identity
      FROM public.calibration_observations observation
      JOIN public.prediction_outcomes outcome
        ON outcome.workspace_id = observation.workspace_id
       AND outcome.tenant_id IS NOT DISTINCT FROM observation.tenant_id
       AND outcome.id::text = observation.source_id
      JOIN public.intelligence_signals signal
        ON signal.workspace_id = outcome.workspace_id
       AND signal.tenant_id IS NOT DISTINCT FROM outcome.tenant_id
       AND signal.signal_id = outcome.signal_id
     WHERE observation.workspace_id = scope_workspace
       AND observation.tenant_id IS NOT DISTINCT FROM scope_tenant
       AND observation.calibration_group = group_value
       AND observation.model_version = version_value
       AND observation.source_type = 'prediction_outcome'
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
       AND outcome.actual_value IS NOT DISTINCT FROM observation.actual_value
       AND signal.metric = observation.predicted_metric
       AND signal.predicted_value IS NOT DISTINCT FROM
           observation.predicted_value
       AND signal.prediction_horizon_days = observation.horizon_days
       AND signal.signal_subtype IN ('future_risk', 'future_opportunity')
       AND observation.id = (
           SELECT min(deduplicated.id)
             FROM public.calibration_observations deduplicated
            WHERE deduplicated.workspace_id = observation.workspace_id
              AND deduplicated.tenant_id IS NOT DISTINCT FROM
                  observation.tenant_id
              AND deduplicated.source_type = observation.source_type
              AND deduplicated.source_id = observation.source_id
              AND deduplicated.calibration_group = observation.calibration_group
              AND deduplicated.model_version = observation.model_version
              AND deduplicated.provenance_status = 'verified'
              AND deduplicated.provenance_reason =
                  'durable_binary_evaluation'
       );
    IF requested_complete AND trusted_total = 0 THEN
        RAISE EXCEPTION 'calibration evidence unavailable'
            USING ERRCODE = '23514';
    END IF;
    SELECT evidence.parent_group, evidence.parent_sample_count,
           evidence.parent_posterior_mean
      INTO parent_group_value, parent_sample_total, parent_mean
      FROM public.authoritative_calibration_parent_evidence(
          scope_workspace, scope_tenant, group_value, version_value
      ) evidence;
    IF group_value LIKE 'source_type:%' AND parent_group_value IS NULL THEN
        RAISE EXCEPTION 'calibration hierarchy unavailable'
            USING ERRCODE = '23514';
    END IF;
    IF parent_sample_total >= 5 AND parent_mean BETWEEN 0 AND 1 THEN
        prior_strength := least(parent_sample_total, 20);
        prior_alpha := 1 + parent_mean * prior_strength;
        prior_beta := 1 + (1 - parent_mean) * prior_strength;
        prior_value := jsonb_build_object(
            'alpha', round(prior_alpha, 6),
            'beta', round(prior_beta, 6),
            'prior_source', CASE
                WHEN parent_group_value LIKE 'global:%' THEN 'global'
                ELSE 'source_type'
            END,
            'partial_pooling_applied', true,
            'parent_calibration_group', parent_group_value,
            'parent_sample_count', parent_sample_total,
            'parent_posterior_mean', round(parent_mean, 6),
            'max_parent_prior_strength', 20,
            'derived_prior_alpha', round(prior_alpha, 6),
            'derived_prior_beta', round(prior_beta, 6)
        );
    ELSE
        prior_value := jsonb_build_object(
            'alpha', 1,
            'beta', 1,
            'prior_source', 'fixed',
            'partial_pooling_applied', false
        );
    END IF;
    posterior_alpha := prior_alpha + hit_total;
    posterior_beta := prior_beta + miss_total;
    posterior_mean := posterior_alpha / (posterior_alpha + posterior_beta);
    posterior_variance := (
        posterior_alpha * posterior_beta
    ) / (
        power(posterior_alpha + posterior_beta, 2)
        * (posterior_alpha + posterior_beta + 1)
    );
    posterior_margin := 1.96 * sqrt(greatest(posterior_variance, 0));
    posterior_value := jsonb_build_object(
        'alpha', round(posterior_alpha, 6),
        'beta', round(posterior_beta, 6),
        'mean', round(posterior_mean, 6),
        'credible_interval', jsonb_build_object(
            'low', round(greatest(0, posterior_mean - posterior_margin), 6),
            'high', round(least(1, posterior_mean + posterior_margin), 6),
            'method', 'beta_normal_approx'
        )
    );
    confidence_value := round(
        least(1, sqrt(greatest(trusted_total, 0)::numeric / 25)), 6
    );
    metrics_value := jsonb_build_object(
        'sample_count', trusted_total,
        'hit_count', hit_total,
        'miss_count', miss_total,
        'partial_count', 0,
        'unknown_count', 0,
        'brier_score', NULL,
        'mae', mae_value,
        'rmse', rmse_value,
        'coverage_p10_p90', NULL,
        'calibration_error', NULL,
        'confidence_score', confidence_value,
        'partial_pooling_applied',
            COALESCE((prior_value->>'partial_pooling_applied')::boolean, false),
        'parent_calibration_group', prior_value->>'parent_calibration_group',
        'parent_sample_count',
            COALESCE((prior_value->>'parent_sample_count')::integer, 0),
        'derived_prior_alpha', prior_value->'derived_prior_alpha',
        'derived_prior_beta', prior_value->'derived_prior_beta',
        'prior_source', prior_value->>'prior_source',
        'eligible_total', trusted_total,
        'processed_total', CASE WHEN requested_complete THEN trusted_total ELSE 0 END,
        'skipped_total', CASE WHEN requested_complete THEN 0 ELSE trusted_total END,
        'skipped_by_reason', CASE WHEN requested_complete THEN '{}'::jsonb ELSE
            jsonb_build_object('authoritative_recompute_required', trusted_total)
        END,
        'complete', requested_complete,
        'provenance_complete', requested_complete,
        'binary_evaluation_complete', requested_complete,
        'reason', CASE WHEN requested_complete THEN NULL
            ELSE 'authoritative_recompute_required'
        END
    );
    state_identity := 'cal-state-' || left(encode(public.digest(
        scope_workspace::text || ':' || group_value || ':' || version_value,
        'sha256'
    ), 'hex'), 32);
    state_hash := encode(public.digest(
        jsonb_build_object(
            'workspace_id', scope_workspace,
            'tenant_id', scope_tenant,
            'calibration_group', group_value,
            'model_version', version_value,
            'prior', prior_value,
            'posterior', posterior_value,
            'metrics', metrics_value,
            'last_observed_at', last_observed,
            'evidence_identity', COALESCE(evidence_identity, '')
        )::text,
        'sha256'
    ), 'hex');
    RETURN QUERY
    INSERT INTO public.calibration_states (
        state_id, tenant_id, workspace_id, calibration_group, model_version,
        prior, posterior, metrics, sample_count, hit_count, miss_count,
        partial_count, unknown_count, brier_score, mae, rmse,
        coverage_p10_p90, calibration_error, confidence_score,
        last_observed_at, reproducibility_hash
    )
    VALUES (
        state_identity, scope_tenant, scope_workspace, group_value, version_value,
        prior_value, posterior_value, metrics_value, trusted_total,
        hit_total, miss_total, 0, 0, NULL, mae_value, rmse_value,
        NULL, NULL, confidence_value, last_observed, state_hash
    )
    ON CONFLICT (workspace_id, calibration_group, model_version) DO UPDATE
    SET prior = EXCLUDED.prior,
        posterior = EXCLUDED.posterior,
        metrics = EXCLUDED.metrics,
        sample_count = EXCLUDED.sample_count,
        hit_count = EXCLUDED.hit_count,
        miss_count = EXCLUDED.miss_count,
        partial_count = EXCLUDED.partial_count,
        unknown_count = EXCLUDED.unknown_count,
        brier_score = EXCLUDED.brier_score,
        mae = EXCLUDED.mae,
        rmse = EXCLUDED.rmse,
        coverage_p10_p90 = EXCLUDED.coverage_p10_p90,
        calibration_error = EXCLUDED.calibration_error,
        confidence_score = EXCLUDED.confidence_score,
        last_observed_at = EXCLUDED.last_observed_at,
        reproducibility_hash = EXCLUDED.reproducibility_hash,
        updated_at = NOW()
    RETURNING *;
END;
$$;
REVOKE INSERT, UPDATE, DELETE ON public.prediction_outcomes FROM omega_console;
REVOKE INSERT, UPDATE, DELETE ON public.calibration_observations
    FROM omega_console;
REVOKE INSERT, UPDATE, DELETE ON public.calibration_states FROM omega_console;
REVOKE USAGE, SELECT ON SEQUENCE public.prediction_outcomes_id_seq
    FROM omega_console;
REVOKE ALL ON FUNCTION public.upsert_calibration_state(JSONB) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.upsert_calibration_state(JSONB)
    TO omega_console;
INSERT INTO schema_migrations(filename, applied_at)
VALUES ('99zzk_calibration_server_recompute.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
