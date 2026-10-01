ALTER FUNCTION omega_publication.assert_current_scope(uuid,uuid)
  SET search_path = pg_catalog, pg_temp;
ALTER FUNCTION omega_publication.gold_compatibility_relation(uuid,uuid,text)
  SET search_path = pg_catalog, pg_temp;
ALTER FUNCTION omega_publication.reserve_materialization(uuid,uuid,uuid,text,text,text,text,uuid)
  SET search_path = pg_catalog, omega_publication, pg_temp;
ALTER FUNCTION omega_publication.submit_verification_candidate(uuid,text,text,text,bigint,jsonb,jsonb)
  SET search_path = pg_catalog, omega_publication, pg_temp;
ALTER FUNCTION omega_publication.load_verification_candidate(uuid)
  SET search_path = pg_catalog, omega_publication, pg_temp;
ALTER FUNCTION omega_publication.create_gold_stage(uuid,jsonb)
  SET search_path = pg_catalog, omega_publication, pg_temp;
ALTER FUNCTION omega_publication.record_attestation(uuid)
  SET search_path = pg_catalog, omega_publication, pg_temp;
ALTER FUNCTION omega_publication.verify_attestation(uuid)
  SET search_path = pg_catalog, omega_publication, pg_temp;
ALTER FUNCTION omega_publication.mark_prepared(uuid,text,text,text,bigint,text,text,jsonb,jsonb)
  SET search_path = pg_catalog, omega_publication, pg_temp;
ALTER FUNCTION omega_publication.quarantine_prepared(uuid,text)
  SET search_path = pg_catalog, omega_publication, pg_temp;
ALTER FUNCTION omega_publication.reopen_prepared_materialization(uuid,text)
  SET search_path = pg_catalog, omega_publication, pg_temp;
ALTER FUNCTION omega_publication.abandon_materialization(uuid)
  SET search_path = pg_catalog, omega_publication, pg_temp;
ALTER FUNCTION omega_publication.publish_materialization(uuid,uuid)
  SET search_path = pg_catalog, omega_publication, pg_temp;
ALTER FUNCTION omega_publication.talent_approval_repair(text,text,text)
  SET search_path = pg_catalog, public, pg_temp;
ALTER FUNCTION omega_publication.talent_approval_constraint(text,text)
  SET search_path = pg_catalog, public, pg_temp;
ALTER FUNCTION omega_publication.enforce_talent_approval_on_publication()
  SET search_path = pg_catalog, public, pg_temp;
ALTER FUNCTION omega_publication.talent_approval_on_create()
  SET search_path = pg_catalog, public, pg_temp;

CREATE TABLE IF NOT EXISTS schema_migrations (
    id BIGSERIAL PRIMARY KEY,
    filename TEXT NOT NULL UNIQUE,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checksum TEXT
);

INSERT INTO schema_migrations(filename, applied_at)
VALUES ('gold/46_publication_search_path_pg_temp_last.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
