-- Durable binary evaluations and explicit reconciliation of legacy calibration.
-- This migration preserves every historical row; unverifiable claims are
-- quarantined and cannot participate in recompute.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE prediction_outcomes
    ADD COLUMN IF NOT EXISTS evaluation_status TEXT,
    ADD COLUMN IF NOT EXISTS evaluation_rule_version TEXT,
    ADD COLUMN IF NOT EXISTS evaluated_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS evaluated_by TEXT;

ALTER TABLE prediction_outcomes
    DROP CONSTRAINT IF EXISTS prediction_outcomes_binary_evaluation_complete,
    ADD CONSTRAINT prediction_outcomes_binary_evaluation_complete CHECK ((
        (
            evaluation_status IS NULL
            AND evaluation_rule_version IS NULL
            AND evaluated_at IS NULL
            AND evaluated_by IS NULL
        )
        OR (
            evaluation_status IN ('hit', 'miss')
            AND NULLIF(btrim(evaluation_rule_version), '') IS NOT NULL
            AND evaluated_at IS NOT NULL
            AND NULLIF(btrim(evaluated_by), '') IS NOT NULL
        )
    ) IS TRUE) NOT VALID;

ALTER TABLE calibration_observations
    ADD COLUMN IF NOT EXISTS provenance_status TEXT NOT NULL
        DEFAULT 'quarantined',
    ADD COLUMN IF NOT EXISTS provenance_reason TEXT NOT NULL
        DEFAULT 'legacy_unreviewed',
    ADD COLUMN IF NOT EXISTS provenance_checked_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS authoritative_calibration_group TEXT,
    ADD COLUMN IF NOT EXISTS evaluation_rule_version TEXT,
    ADD COLUMN IF NOT EXISTS legacy_claims JSONB;

ALTER TABLE calibration_observations
    DROP CONSTRAINT IF EXISTS calibration_observations_provenance_status_check,
    ADD CONSTRAINT calibration_observations_provenance_status_check
        CHECK (provenance_status IN ('verified', 'quarantined')) NOT VALID;

-- Preserve the claims as originally stored before reconciling verifiable rows.
UPDATE calibration_observations
   SET legacy_claims = jsonb_build_object(
           'predicted_metric', predicted_metric,
           'predicted_probability', predicted_probability,
           'predicted_value', predicted_value,
           'predicted_interval', predicted_interval,
           'actual_value', actual_value,
           'actual_status', actual_status,
           'observed_at', observed_at,
           'horizon_days', horizon_days,
           'calibration_group', calibration_group,
           'prior', prior,
           'posterior', posterior,
           'metrics', metrics,
           'reproducibility_hash', reproducibility_hash
       )
 WHERE legacy_claims IS NULL
   AND provenance_reason = 'legacy_unreviewed';

WITH durable AS (
    SELECT observation.id AS observation_pk,
           outcome.id::text AS outcome_id,
           outcome.actual_value,
           outcome.evaluation_status,
           outcome.evaluation_rule_version,
           outcome.evaluated_at,
           signal.metric,
           signal.predicted_value,
           signal.prediction_horizon_days,
           (
               'source_type:'
               || regexp_replace(lower(btrim(signal.metadata->>'source_system')),
                                 '[^a-z0-9_-]', '_', 'g')
               || ':'
               || regexp_replace(lower(btrim(signal.metric)),
                                 '[^a-z0-9_-]', '_', 'g')
               || ':v1'
           ) AS authoritative_group
      FROM calibration_observations observation
      JOIN prediction_outcomes outcome
        ON outcome.workspace_id = observation.workspace_id
       AND outcome.tenant_id IS NOT DISTINCT FROM observation.tenant_id
       AND outcome.id::text = observation.source_id
      JOIN intelligence_signals signal
        ON signal.workspace_id = outcome.workspace_id
       AND signal.tenant_id IS NOT DISTINCT FROM outcome.tenant_id
       AND signal.signal_id = outcome.signal_id
     WHERE observation.source_type = 'prediction_outcome'
       AND outcome.evaluation_status IN ('hit', 'miss')
       AND outcome.evaluation_rule_version IS NOT NULL
       AND outcome.evaluated_at IS NOT NULL
       AND outcome.evaluated_by IS NOT NULL
       AND signal.signal_subtype = 'observed'
       AND signal.prediction_horizon_days BETWEEN 1 AND 3650
       AND NULLIF(btrim(signal.metadata->>'source_system'), '') IS NOT NULL
       AND NULLIF(btrim(signal.metadata->>'source_dataset'), '') IS NOT NULL
       AND NULLIF(btrim(signal.metadata->>'evidence_pack_id'), '') IS NOT NULL
), ranked AS (
    SELECT durable.*,
           row_number() OVER (
               PARTITION BY observation_pk IS NOT NULL,
                            outcome_id, authoritative_group
               ORDER BY observation_pk
           ) AS authority_rank
      FROM durable
      JOIN calibration_observations observation
        ON observation.id = durable.observation_pk
     WHERE length(durable.authoritative_group) <= 80
       AND observation.calibration_group = durable.authoritative_group
), eligible AS (
    SELECT * FROM ranked WHERE authority_rank = 1
)
UPDATE calibration_observations observation
   SET provenance_status = 'verified',
       provenance_reason = 'legacy_reconciled_from_durable_outcome',
       provenance_checked_at = NOW(),
       authoritative_calibration_group = eligible.authoritative_group,
       evaluation_rule_version = eligible.evaluation_rule_version,
       predicted_metric = eligible.metric,
       predicted_value = eligible.predicted_value,
       actual_value = eligible.actual_value,
       actual_status = eligible.evaluation_status,
       observed_at = eligible.evaluated_at,
       horizon_days = eligible.prediction_horizon_days,
       idempotency_key = COALESCE(
           observation.idempotency_key,
           'cal-legacy-' || encode(
               digest(
                   observation.workspace_id::text || ':' || eligible.outcome_id
                   || ':' || eligible.authoritative_group,
                   'sha256'
               ),
               'hex'
           )
       ),
       evidence_digest = COALESCE(
           observation.evidence_digest,
           encode(
               digest(
                   eligible.outcome_id || ':' || eligible.evaluation_status
                   || ':' || eligible.evaluation_rule_version
                   || ':' || eligible.evaluated_at::text,
                   'sha256'
               ),
               'hex'
           )
       )
  FROM eligible
 WHERE observation.id = eligible.observation_pk
   AND (
       observation.provenance_status <> 'verified'
       OR observation.provenance_reason = 'legacy_unreviewed'
   );

UPDATE calibration_observations
   SET provenance_status = 'quarantined',
       provenance_reason = CASE
           WHEN source_type = 'decision_option'
               THEN 'decision_option_is_not_authoritative_evidence'
           WHEN source_type = 'prediction_outcome'
               THEN 'durable_binary_evaluation_unavailable'
           ELSE 'legacy_source_not_authoritatively_reconstructible'
       END,
       provenance_checked_at = COALESCE(provenance_checked_at, NOW()),
       authoritative_calibration_group = NULL,
       evaluation_rule_version = NULL
 WHERE provenance_status <> 'verified';

UPDATE calibration_states
   SET metrics = COALESCE(metrics, '{}'::jsonb) || jsonb_build_object(
           'complete', false,
           'provenance_complete', false,
           'binary_evaluation_complete', false,
           'reason', 'authoritative_recompute_required'
       ),
       updated_at = NOW()
 WHERE COALESCE((metrics->>'binary_evaluation_complete')::boolean, false) IS NOT TRUE
   AND NOT COALESCE(metrics, '{}'::jsonb) @> jsonb_build_object(
       'complete', false,
       'provenance_complete', false,
       'binary_evaluation_complete', false,
       'reason', 'authoritative_recompute_required'
   );

CREATE UNIQUE INDEX IF NOT EXISTS
    calibration_observations_verified_source_uidx
    ON calibration_observations(
        workspace_id, source_type, source_id, model_version
    )
    WHERE provenance_status = 'verified'
      AND source_type = 'prediction_outcome';

REVOKE UPDATE, DELETE ON prediction_outcomes FROM omega_console;
REVOKE INSERT ON prediction_outcomes FROM omega_console;
GRANT SELECT ON prediction_outcomes TO omega_console;
GRANT INSERT (
    tenant_id, workspace_id, signal_id, option_id, action_taken,
    predicted_value, actual_value, prediction_error, outcome_summary,
    learned_rule, metadata, owner_user_id
) ON prediction_outcomes TO omega_console;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zze_calibration_authoritative_reconciliation.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
