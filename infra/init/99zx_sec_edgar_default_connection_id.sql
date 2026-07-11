-- SEC EDGAR uses the workspace-scoped Vault connection saved as conn_id=default.

UPDATE entity_config
   SET connection_id = 'default'
 WHERE cartridge_id = 'sec_edgar'
   AND entity = 'company_facts'
   AND COALESCE(NULLIF(connection_id, ''), '') = '';
