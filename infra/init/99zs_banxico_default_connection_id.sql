-- Banxico uses the workspace-scoped Vault connection saved as conn_id=default.
-- Existing PR 2A seeds did not backfill entity_config.connection_id, so UI
-- extraction triggers could omit conn_id and the production DAG would fail.

UPDATE entity_config
   SET connection_id = 'default'
 WHERE cartridge_id = 'banxico'
   AND entity = 'series_observations'
   AND COALESCE(NULLIF(connection_id, ''), '') = '';
