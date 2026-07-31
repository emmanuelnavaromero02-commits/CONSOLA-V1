-- Durable, append-only identities for Bayesian calibration observations.
--
-- Legacy rows remain nullable because their identity depended on mutable
-- posterior state and cannot be reconstructed without inventing evidence.

ALTER TABLE calibration_observations
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT,
    ADD COLUMN IF NOT EXISTS evidence_digest TEXT;

ALTER TABLE calibration_observations
    DROP CONSTRAINT IF EXISTS calibration_observations_idempotency_key_nonempty,
    ADD CONSTRAINT calibration_observations_idempotency_key_nonempty
        CHECK (idempotency_key IS NULL OR btrim(idempotency_key) <> '') NOT VALID;

ALTER TABLE calibration_observations
    DROP CONSTRAINT IF EXISTS calibration_observations_evidence_digest_nonempty,
    ADD CONSTRAINT calibration_observations_evidence_digest_nonempty
        CHECK (evidence_digest IS NULL OR btrim(evidence_digest) <> '') NOT VALID;

CREATE UNIQUE INDEX IF NOT EXISTS
    calibration_observations_workspace_idempotency_uidx
    ON calibration_observations(workspace_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

REVOKE UPDATE, DELETE ON calibration_observations FROM omega_console;
REVOKE UPDATE, DELETE ON prediction_outcomes FROM omega_console;
GRANT SELECT, INSERT ON calibration_observations TO omega_console;
GRANT SELECT, INSERT ON prediction_outcomes TO omega_console;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zzd_calibration_observation_idempotency.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
