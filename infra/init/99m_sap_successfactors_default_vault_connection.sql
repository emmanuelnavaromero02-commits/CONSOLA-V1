-- 99m_sap_successfactors_default_vault_connection.sql
--
-- SuccessFactors extraction must select a Vault connection so the cartridge
-- gets the external base URL, OAuth/SAML credentials, and private key from
-- Vault instead of falling back to incomplete worker env defaults.

UPDATE entity_config
   SET connection_id = 'femsa_sf'
 WHERE cartridge_id = 'sap_successfactors'
   AND enabled IS TRUE
   AND COALESCE(NULLIF(connection_id, ''), '') = '';

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99m_sap_successfactors_default_vault_connection.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
