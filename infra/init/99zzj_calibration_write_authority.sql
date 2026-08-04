-- Close direct calibration DML and expose scoped server-owned writers only.

CREATE OR REPLACE FUNCTION public.record_calibration_observation(
    p_payload JSONB
)
RETURNS SETOF public.calibration_observations
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    scope_workspace UUID;
    scope_tenant UUID;
BEGIN
    scope_workspace := NULLIF(
        current_setting('app.workspace_id', true), ''
    )::uuid;
    scope_tenant := NULLIF(current_setting('app.tenant_id', true), '')::uuid;
    IF scope_workspace IS NULL THEN
        RAISE EXCEPTION 'calibration scope denied' USING ERRCODE = '42501';
    END IF;
    IF jsonb_typeof(p_payload) <> 'object' THEN
        RAISE EXCEPTION 'calibration observation payload invalid'
            USING ERRCODE = '22023';
    END IF;

    RETURN QUERY
    INSERT INTO public.calibration_observations (
        observation_id, idempotency_key, evidence_digest,
        tenant_id, workspace_id, source_type, source_id,
        predicted_metric, predicted_value, predicted_interval,
        predicted_probability, actual_value, actual_status, observed_at,
        horizon_days, model_version, calibration_group, prior, posterior,
        metrics, evidence_refs, explanation, reproducibility_hash, created_by
    )
    VALUES (
        p_payload->>'observation_id',
        p_payload->>'idempotency_key',
        p_payload->>'evidence_digest',
        scope_tenant,
        scope_workspace,
        p_payload->>'source_type',
        p_payload->>'source_id',
        p_payload->>'predicted_metric',
        NULLIF(p_payload->>'predicted_value', '')::numeric,
        COALESCE(p_payload->'predicted_interval', '{}'::jsonb),
        NULLIF(p_payload->>'predicted_probability', '')::numeric,
        NULLIF(p_payload->>'actual_value', '')::numeric,
        p_payload->>'actual_status',
        NULLIF(p_payload->>'observed_at', '')::timestamptz,
        NULLIF(p_payload->>'horizon_days', '')::integer,
        p_payload->>'model_version',
        p_payload->>'calibration_group',
        COALESCE(p_payload->'prior', '{}'::jsonb),
        COALESCE(p_payload->'posterior', '{}'::jsonb),
        COALESCE(p_payload->'metrics', '{}'::jsonb),
        COALESCE(p_payload->'evidence_refs', '[]'::jsonb),
        p_payload->>'explanation',
        p_payload->>'reproducibility_hash',
        NULLIF(p_payload->>'created_by', '')::bigint
    )
    ON CONFLICT DO NOTHING
    RETURNING *;
END;
$$;

CREATE OR REPLACE FUNCTION public.upsert_calibration_state(
    p_payload JSONB
)
RETURNS SETOF public.calibration_states
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    scope_workspace UUID;
    scope_tenant UUID;
    metrics_value JSONB;
    group_value TEXT;
    version_value TEXT;
    sample_total INTEGER;
    processed_total INTEGER;
    eligible_total INTEGER;
    skipped_total INTEGER;
    verified_total INTEGER;
BEGIN
    scope_workspace := NULLIF(
        current_setting('app.workspace_id', true), ''
    )::uuid;
    scope_tenant := NULLIF(current_setting('app.tenant_id', true), '')::uuid;
    IF scope_workspace IS NULL THEN
        RAISE EXCEPTION 'calibration scope denied' USING ERRCODE = '42501';
    END IF;
    IF jsonb_typeof(p_payload) <> 'object'
       OR jsonb_typeof(p_payload->'metrics') <> 'object'
    THEN
        RAISE EXCEPTION 'calibration state payload invalid'
            USING ERRCODE = '22023';
    END IF;

    metrics_value := p_payload->'metrics';
    group_value := p_payload->>'calibration_group';
    version_value := p_payload->>'model_version';
    sample_total := COALESCE((metrics_value->>'sample_count')::integer, 0);
    processed_total := COALESCE((metrics_value->>'processed_total')::integer, 0);
    eligible_total := COALESCE((metrics_value->>'eligible_total')::integer, 0);
    skipped_total := COALESCE((metrics_value->>'skipped_total')::integer, 0);

    IF COALESCE((metrics_value->>'complete')::boolean, false) THEN
        IF COALESCE(
            (metrics_value->>'provenance_complete')::boolean, false
        ) IS NOT TRUE
           OR COALESCE(
               (metrics_value->>'binary_evaluation_complete')::boolean, false
           ) IS NOT TRUE
           OR processed_total <= 0
           OR processed_total <> eligible_total
           OR processed_total <> sample_total
           OR skipped_total <> 0
        THEN
            RAISE EXCEPTION 'calibration coverage incomplete'
                USING ERRCODE = '23514';
        END IF;
        SELECT count(*)::integer
          INTO verified_total
          FROM public.calibration_observations observation
         WHERE observation.workspace_id = scope_workspace
           AND observation.tenant_id IS NOT DISTINCT FROM scope_tenant
           AND observation.calibration_group = group_value
           AND observation.model_version = version_value
           AND observation.provenance_status = 'verified'
           AND observation.provenance_reason = 'durable_binary_evaluation'
           AND observation.authoritative_calibration_group =
               observation.calibration_group;
        IF verified_total <> processed_total THEN
            RAISE EXCEPTION 'calibration evidence coverage mismatch'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    RETURN QUERY
    INSERT INTO public.calibration_states (
        state_id, tenant_id, workspace_id, calibration_group, model_version,
        prior, posterior, metrics, sample_count, hit_count, miss_count,
        partial_count, unknown_count, brier_score, mae, rmse,
        coverage_p10_p90, calibration_error, confidence_score,
        last_observed_at, reproducibility_hash
    )
    VALUES (
        p_payload->>'state_id', scope_tenant, scope_workspace,
        group_value, version_value,
        COALESCE(p_payload->'prior', '{}'::jsonb),
        COALESCE(p_payload->'posterior', '{}'::jsonb),
        metrics_value, sample_total,
        COALESCE((metrics_value->>'hit_count')::integer, 0),
        COALESCE((metrics_value->>'miss_count')::integer, 0),
        COALESCE((metrics_value->>'partial_count')::integer, 0),
        COALESCE((metrics_value->>'unknown_count')::integer, 0),
        NULLIF(metrics_value->>'brier_score', '')::numeric,
        NULLIF(metrics_value->>'mae', '')::numeric,
        NULLIF(metrics_value->>'rmse', '')::numeric,
        NULLIF(metrics_value->>'coverage_p10_p90', '')::numeric,
        NULLIF(metrics_value->>'calibration_error', '')::numeric,
        NULLIF(metrics_value->>'confidence_score', '')::numeric,
        NULLIF(p_payload->>'last_observed_at', '')::timestamptz,
        p_payload->>'reproducibility_hash'
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
        last_observed_at = COALESCE(
            EXCLUDED.last_observed_at,
            public.calibration_states.last_observed_at
        ),
        reproducibility_hash = EXCLUDED.reproducibility_hash,
        updated_at = NOW()
    RETURNING *;
END;
$$;

REVOKE INSERT, UPDATE, DELETE ON public.prediction_outcomes FROM omega_console;
REVOKE INSERT (
    tenant_id, workspace_id, signal_id, option_id, action_taken,
    predicted_value, actual_value, prediction_error, outcome_summary,
    learned_rule, metadata, owner_user_id
) ON public.prediction_outcomes FROM omega_console;
REVOKE INSERT, UPDATE, DELETE ON public.calibration_observations
    FROM omega_console;
REVOKE INSERT (
    observation_id, idempotency_key, evidence_digest,
    tenant_id, workspace_id, source_type, source_id,
    predicted_metric, predicted_value, predicted_interval,
    predicted_probability, actual_value, actual_status, observed_at,
    horizon_days, model_version, calibration_group, prior, posterior,
    metrics, evidence_refs, explanation, reproducibility_hash, created_by
) ON public.calibration_observations FROM omega_console;
REVOKE INSERT, UPDATE, DELETE ON public.calibration_states FROM omega_console;
REVOKE USAGE, SELECT ON SEQUENCE public.calibration_observations_id_seq
    FROM omega_console;
REVOKE USAGE, SELECT ON SEQUENCE public.calibration_states_id_seq
    FROM omega_console;

REVOKE ALL ON FUNCTION public.record_calibration_observation(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.upsert_calibration_state(JSONB) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.record_calibration_observation(JSONB)
    TO omega_console;
GRANT EXECUTE ON FUNCTION public.upsert_calibration_state(JSONB)
    TO omega_console;
GRANT SELECT ON public.prediction_outcomes TO omega_console;
GRANT SELECT ON public.calibration_observations TO omega_console;
GRANT SELECT ON public.calibration_states TO omega_console;

INSERT INTO schema_migrations(filename, applied_at)
VALUES ('99zzj_calibration_write_authority.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
