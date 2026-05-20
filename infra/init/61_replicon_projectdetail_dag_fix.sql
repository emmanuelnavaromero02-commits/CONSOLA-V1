-- Historical compatibility: old beta metadata pointed ProjectDetail at
-- replicon_projects_detail. The current Replicon cartridge owns file-based
-- ProjectDetail ingestion through file_ingest.

UPDATE entity_config
   SET dag_id = 'file_ingest',
       connection_id = 'services',
       dag_params = COALESCE(NULLIF(dag_params, '{}'::jsonb),
           '{"file_pattern":"VO_PROJECTS_DETAIL*.csv","format":"csv","delimiter":",","encoding":"utf-8","parser":"default"}'::jsonb)
 WHERE cartridge_id = 'replicon'
   AND entity = 'ProjectDetail'
   AND COALESCE(dag_id, '') <> 'file_ingest';

DELETE FROM cartridge_dags
 WHERE cartridge_id = 'replicon'
   AND dag_id = 'replicon_projects_detail';
