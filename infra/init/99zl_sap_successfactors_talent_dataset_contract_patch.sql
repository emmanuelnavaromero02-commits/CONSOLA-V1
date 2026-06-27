-- 99zl_sap_successfactors_talent_dataset_contract_patch.sql
--
-- Patch already-seeded dataset SQL definitions without relying on old
-- migrations being re-applied during deploy.

UPDATE datasets
   SET sql_def = REPLACE(
       sql_def,
       E'SELECT\n    emp.user_id,',
       E'SELECT\n    emp.tenant_id,\n    emp.workspace_id,\n    emp.user_id,'
   ),
       updated_at = NOW()
 WHERE cartridge = 'sap_successfactors'
   AND name = 'sap_successfactors_talent_employee_profile'
   AND sql_def LIKE E'%SELECT\n    emp.user_id,%';

UPDATE datasets
   SET sql_def = REPLACE(
       REPLACE(
         sql_def,
         E'SELECT\n        emp.user_id,',
         E'SELECT\n        emp.tenant_id,\n        emp.workspace_id,\n        emp.user_id,'
       ),
       E'\nSELECT\n    user_id,',
       E'\nSELECT\n    tenant_id,\n    workspace_id,\n    user_id,'
   ),
       updated_at = NOW()
 WHERE cartridge = 'sap_successfactors'
   AND name = 'sap_successfactors_talent_cpa_scores';

UPDATE datasets
   SET sql_def = REPLACE(
       REPLACE(
         REPLACE(
           sql_def,
           'WHEN readiness_status IN (''ready'', ''partial'', ''benchmark_internal'') THEN 3',
           'WHEN readiness_status IN (''ready'', ''benchmark_internal'') THEN 3'
         ),
         E'WHEN readiness_status = ''benchmark_internal'' THEN ''benchmark_internal''\n        WHEN readiness_status = ''partial'' THEN ''partial''\n        WHEN readiness_status = ''ready'' THEN ''ready''',
         E'WHEN readiness_status IN (''ready'', ''benchmark_internal'') THEN ''ready''\n        WHEN readiness_status = ''partial'' THEN ''partial'''
       ),
       E'WHEN readiness_status = ''insufficient_data'' THEN ''missing_simulation_inputs''',
       E'WHEN readiness_status = ''partial'' THEN ''missing_simulation_inputs''\n        WHEN readiness_status = ''insufficient_data'' THEN ''missing_simulation_inputs'''
   ),
       updated_at = NOW()
 WHERE cartridge = 'sap_successfactors'
   AND name = 'sap_successfactors_talent_simulation_inputs';

INSERT INTO schema_migrations(filename, applied_at)
VALUES ('99zl_sap_successfactors_talent_dataset_contract_patch.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
