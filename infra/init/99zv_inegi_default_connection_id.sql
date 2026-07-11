-- INEGI uses the workspace-scoped Vault connection saved as conn_id=default.

UPDATE entity_config
   SET connection_id = 'default'
 WHERE cartridge_id = 'inegi'
   AND entity = 'series_observations'
   AND COALESCE(NULLIF(connection_id, ''), '') = '';
