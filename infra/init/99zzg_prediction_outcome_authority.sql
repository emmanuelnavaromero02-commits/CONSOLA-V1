-- Server-owned outcome identity and deterministic directional evaluation.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE prediction_outcomes
    ADD COLUMN IF NOT EXISTS outcome_identity TEXT;

WITH ranked AS (
    SELECT id, workspace_id, signal_id,
           row_number() OVER (
               PARTITION BY workspace_id, signal_id ORDER BY id
           ) AS identity_rank
      FROM prediction_outcomes
     WHERE outcome_identity IS NULL
)
UPDATE prediction_outcomes outcome
   SET outcome_identity = encode(
       digest(
           ranked.workspace_id::text || ':' || ranked.signal_id
           || CASE WHEN ranked.identity_rank = 1
                   THEN '' ELSE ':' || outcome.id::text END,
           'sha256'
       ),
       'hex'
   )
  FROM ranked
 WHERE outcome.id = ranked.id;

CREATE UNIQUE INDEX IF NOT EXISTS prediction_outcomes_identity_uidx
    ON prediction_outcomes(workspace_id, outcome_identity)
    WHERE outcome_identity IS NOT NULL;

CREATE OR REPLACE FUNCTION public.record_prediction_outcome(
    p_tenant_id UUID,
    p_workspace_id UUID,
    p_signal_id TEXT,
    p_option_id TEXT,
    p_action_taken TEXT,
    p_reported_actual_value NUMERIC,
    p_outcome_summary TEXT,
    p_learned_rule TEXT,
    p_owner_user_id BIGINT,
    p_metadata JSONB
)
RETURNS TABLE(outcome JSONB, inserted BOOLEAN)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    signal_record RECORD;
    observed_record RECORD;
    existing_row public.prediction_outcomes%ROWTYPE;
    inserted_row public.prediction_outcomes%ROWTYPE;
    signal_found BOOLEAN;
    identity_value TEXT;
    authoritative_prediction NUMERIC;
    authoritative_actual NUMERIC;
    prediction_error_value NUMERIC;
    evaluation_value TEXT;
    evaluation_rule TEXT;
    evaluation_time TIMESTAMPTZ;
    evaluated_actor TEXT;
    server_metadata JSONB;
BEGIN
    IF p_workspace_id::text IS DISTINCT FROM
       NULLIF(current_setting('app.workspace_id', true), '')
       OR COALESCE(p_tenant_id::text, '') IS DISTINCT FROM
          COALESCE(NULLIF(current_setting('app.tenant_id', true), ''), '')
    THEN
        RAISE EXCEPTION 'outcome scope denied' USING ERRCODE = '42501';
    END IF;
    IF NULLIF(btrim(p_signal_id), '') IS NULL
       OR NULLIF(btrim(p_action_taken), '') IS NULL
       OR NULLIF(btrim(p_outcome_summary), '') IS NULL
    THEN
        RAISE EXCEPTION 'outcome fields incomplete' USING ERRCODE = '23514';
    END IF;

    SELECT signal.metric, signal.dataset, signal.entity_kind, signal.entity_id,
           signal.expected_value, signal.predicted_value, signal.created_at,
           signal.prediction_horizon_days, signal.signal_subtype,
           signal.metadata
      INTO signal_record
      FROM public.intelligence_signals signal
     WHERE signal.workspace_id = p_workspace_id
       AND signal.tenant_id IS NOT DISTINCT FROM p_tenant_id
       AND signal.signal_id = p_signal_id
     FOR SHARE;
    signal_found := FOUND;

    IF p_option_id IS NOT NULL AND signal_found AND NOT EXISTS (
        SELECT 1 FROM public.decision_options option_row
         WHERE option_row.workspace_id = p_workspace_id
           AND option_row.tenant_id IS NOT DISTINCT FROM p_tenant_id
           AND option_row.signal_id = p_signal_id
           AND (
               option_row.id::text = p_option_id
               OR option_row.option_id = p_option_id
           )
    ) THEN
        RAISE EXCEPTION 'outcome option is not scoped to signal'
            USING ERRCODE = '23503';
    END IF;
    IF p_option_id IS NOT NULL AND NOT signal_found THEN
        RAISE EXCEPTION 'outcome option requires an intelligence signal'
            USING ERRCODE = '23503';
    END IF;

    IF NOT signal_found AND NOT EXISTS (
        SELECT 1 FROM public.control_room_items item
         WHERE item.workspace_id = p_workspace_id
           AND item.tenant_id IS NOT DISTINCT FROM p_tenant_id
           AND item.item_id = p_signal_id
    ) THEN
        RAISE EXCEPTION 'outcome source unavailable' USING ERRCODE = '23503';
    END IF;
    IF jsonb_typeof(COALESCE(p_metadata, '{}'::jsonb)) <> 'object' THEN
        RAISE EXCEPTION 'outcome metadata must be an object'
            USING ERRCODE = '23514';
    END IF;
    IF p_reported_actual_value::text IN ('NaN', 'Infinity', '-Infinity') THEN
        RAISE EXCEPTION 'outcome actual value must be finite'
            USING ERRCODE = '23514';
    END IF;

    authoritative_prediction := signal_record.predicted_value;
    IF signal_found THEN
        SELECT observed.signal_id, observed.actual_value,
               evidence.observed_at, observed.metadata
          INTO observed_record
          FROM public.intelligence_signals observed
          CROSS JOIN LATERAL (
              SELECT (reference->>'observed_at')::timestamptz AS observed_at
                FROM jsonb_array_elements(
                    CASE
                        WHEN jsonb_typeof(observed.metadata->'evidence_refs') =
                             'array'
                        THEN observed.metadata->'evidence_refs'
                        ELSE '[]'::jsonb
                    END
                ) AS reference
               WHERE reference->>'type' = 'dataset_row'
                 AND reference->>'attestation_version' = 'hmac-sha256-v4'
                 AND reference->>'attestation_purpose' =
                     'control-room-runtime-evidence-v1'
                 AND NULLIF(btrim(reference->>'server_attestation'), '') IS NOT NULL
                 AND reference->>'scope_binding' = encode(
                     public.digest(
                         p_tenant_id::text || chr(31) || p_workspace_id::text,
                         'sha256'
                     ),
                     'hex'
                 )
                 AND reference->>'observed_at' ~
                     '^[0-9]{4}-[0-9]{2}-[0-9]{2}T'
               ORDER BY reference->>'observed_at' DESC
               LIMIT 1
          ) evidence
         WHERE observed.workspace_id = p_workspace_id
           AND observed.tenant_id IS NOT DISTINCT FROM p_tenant_id
           AND observed.signal_subtype = 'observed'
           AND observed.metric = signal_record.metric
           AND observed.dataset = signal_record.dataset
           AND observed.entity_kind = signal_record.entity_kind
           AND observed.entity_id = signal_record.entity_id
           AND observed.metadata->>'source_system' =
               signal_record.metadata->>'source_system'
           AND observed.metadata->>'source_dataset' =
               signal_record.metadata->>'source_dataset'
           AND observed.actual_value IS NOT NULL
           AND evidence.observed_at >= signal_record.created_at
               + make_interval(days => signal_record.prediction_horizon_days)
           AND NULLIF(btrim(observed.metadata->>'evidence_pack_id'), '') IS NOT NULL
           AND CASE
               WHEN jsonb_typeof(observed.metadata->'evidence_refs') = 'array'
               THEN jsonb_array_length(observed.metadata->'evidence_refs') > 0
               ELSE false
           END
         ORDER BY evidence.observed_at DESC, observed.signal_id
         LIMIT 1
         FOR SHARE;
    END IF;
    authoritative_actual := CASE
        WHEN observed_record.signal_id IS NOT NULL
        THEN observed_record.actual_value
        ELSE p_reported_actual_value
    END;
    IF observed_record.signal_id IS NOT NULL
       AND p_reported_actual_value IS NOT NULL
       AND p_reported_actual_value IS DISTINCT FROM observed_record.actual_value
    THEN
        RAISE EXCEPTION 'reported outcome conflicts with observed evidence'
            USING ERRCODE = '23514';
    END IF;
    IF observed_record.signal_id IS NOT NULL
       AND authoritative_actual IS NOT NULL
       AND authoritative_prediction IS NOT NULL
    THEN
        prediction_error_value := round(
            authoritative_actual - authoritative_prediction, 4
        );
    END IF;

    IF signal_record.signal_subtype IN ('future_risk', 'future_opportunity')
       AND signal_record.prediction_horizon_days BETWEEN 1 AND 3650
       AND observed_record.signal_id IS NOT NULL
       AND signal_record.predicted_value IS NOT NULL
       AND signal_record.expected_value IS NOT NULL
       AND signal_record.predicted_value <> signal_record.expected_value
       AND NULLIF(btrim(signal_record.metric), '') IS NOT NULL
       AND NULLIF(btrim(signal_record.metadata->>'source_system'), '') IS NOT NULL
       AND NULLIF(btrim(signal_record.metadata->>'source_dataset'), '') IS NOT NULL
       AND NULLIF(btrim(signal_record.metadata->>'evidence_pack_id'), '') IS NOT NULL
    THEN
        evaluation_value := CASE
            WHEN sign(signal_record.predicted_value - signal_record.expected_value)
                 = sign(authoritative_actual - signal_record.expected_value)
            THEN 'hit' ELSE 'miss'
        END;
        evaluation_rule := 'expected_direction_match.v1';
        evaluation_time := observed_record.observed_at;
        evaluated_actor := 'omega_outcome_evaluator.v1';
    END IF;

    identity_value := encode(
        public.digest(
            p_workspace_id::text || ':' || p_signal_id || ':'
            || CASE WHEN evaluation_value IS NULL THEN 'reported' ELSE 'observed' END,
            'sha256'
        ),
        'hex'
    );
    server_metadata := COALESCE(p_metadata, '{}'::jsonb) || jsonb_build_object(
        'source_type', 'prediction_outcome',
        'input_classification', CASE
            WHEN evaluation_value IS NULL THEN 'reported_outcome' ELSE 'observed'
        END,
        'observed', evaluation_value IS NOT NULL,
        'source_system', signal_record.metadata->>'source_system',
        'source_dataset', signal_record.metadata->>'source_dataset',
        'observed_signal_id', observed_record.signal_id,
        'evidence_refs', COALESCE(
            observed_record.metadata->'evidence_refs', '[]'::jsonb
        )
    );

    INSERT INTO public.prediction_outcomes (
        tenant_id, workspace_id, signal_id, option_id, action_taken,
        predicted_value, actual_value, prediction_error, outcome_summary,
        learned_rule, owner_user_id, metadata, outcome_identity,
        evaluation_status, evaluation_rule_version, evaluated_at, evaluated_by
    )
    VALUES (
        p_tenant_id, p_workspace_id, p_signal_id, p_option_id, p_action_taken,
        authoritative_prediction, authoritative_actual, prediction_error_value,
        p_outcome_summary,
        CASE WHEN evaluation_value IS NOT NULL THEN p_learned_rule ELSE NULL END,
        p_owner_user_id, server_metadata,
        identity_value, evaluation_value, evaluation_rule, evaluation_time,
        evaluated_actor
    )
    ON CONFLICT (workspace_id, outcome_identity)
        WHERE outcome_identity IS NOT NULL
    DO NOTHING
    RETURNING * INTO inserted_row;

    IF inserted_row.id IS NOT NULL THEN
        RETURN QUERY SELECT to_jsonb(inserted_row), true;
        RETURN;
    END IF;
    SELECT * INTO existing_row
      FROM public.prediction_outcomes
     WHERE workspace_id = p_workspace_id
       AND outcome_identity = identity_value
     FOR SHARE;
    IF existing_row.actual_value IS DISTINCT FROM authoritative_actual THEN
        RAISE EXCEPTION 'outcome identity conflict' USING ERRCODE = '23505';
    END IF;
    RETURN QUERY SELECT to_jsonb(existing_row), false;
END;
$$;

REVOKE ALL ON FUNCTION public.record_prediction_outcome(
    UUID, UUID, TEXT, TEXT, TEXT, NUMERIC, TEXT, TEXT, BIGINT, JSONB
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.record_prediction_outcome(
    UUID, UUID, TEXT, TEXT, TEXT, NUMERIC, TEXT, TEXT, BIGINT, JSONB
) TO omega_console;
REVOKE INSERT, UPDATE, DELETE ON prediction_outcomes FROM omega_console;
GRANT SELECT ON prediction_outcomes TO omega_console;

INSERT INTO schema_migrations(filename, applied_at)
VALUES ('99zzg_prediction_outcome_authority.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
