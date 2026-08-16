-- 99zzx_sap_sf_dataset_sources_repair.sql
--
-- F7-2: repair drifted `sources` declarations on four SuccessFactors dataset
-- rows. The hardened engine keeps dataset SQL portable (unscoped globs) and
-- rewrites every DECLARED source to the tenant/workspace physical path at
-- materialization (_scope_storage_sql); a storage literal the SQL reads but
-- `sources` does not declare is never rewritten, so the scope validator
-- (correctly) rejects it with "S3 path is outside the caller
-- tenant/workspace scope". Seed 82 shipped four rows whose SQL had been
-- migrated to read gold/silver while their declarations kept an older list
-- (82 stays untouched: it is checksum-recorded in schema_migrations).
--
-- The lists below match the canonical cartridge dataset files
-- (cartridges/sap_successfactors/datasets/*.sql), which a contract test now
-- keeps in sync with each file's actual storage literals.

UPDATE datasets
   SET sources = '["gold/sap_successfactors/sap_successfactors_employee_360", "silver/sap_successfactors/sap_successfactors_fojobcode_latest"]'::jsonb,
       updated_at = NOW()
 WHERE cartridge = 'sap_successfactors'
   AND name = 'sap_successfactors_employees_anomalies';

UPDATE datasets
   SET sources = '["gold/sap_successfactors/sap_successfactors_employee_360"]'::jsonb,
       updated_at = NOW()
 WHERE cartridge = 'sap_successfactors'
   AND name = 'sap_successfactors_manager_hierarchy';

UPDATE datasets
   SET sources = '["raw/sap_successfactors/JobRequisition", "raw/sap_successfactors/Candidate", "silver/sap_successfactors/sap_successfactors_recruitment_pipeline"]'::jsonb,
       updated_at = NOW()
 WHERE cartridge = 'sap_successfactors'
   AND name = 'sap_successfactors_recruitment_funnel';

UPDATE datasets
   SET sources = '["silver/sap_successfactors/sap_successfactors_empemploymenttermination_latest", "silver/sap_successfactors/sap_successfactors_foeventreason_latest"]'::jsonb,
       updated_at = NOW()
 WHERE cartridge = 'sap_successfactors'
   AND name = 'sap_successfactors_turnover_by_period';

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zzx_sap_sf_dataset_sources_repair.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
