-- knowledge_bits tables: FORCE RLS on tenant/workspace text columns, or quarantine.

CREATE OR REPLACE FUNCTION omega_rls_workspace_text_matches(row_tenant text, row_workspace text)
RETURNS boolean
LANGUAGE sql
STABLE
AS $$
    SELECT row_tenant IS NOT NULL
       AND row_workspace IS NOT NULL
       AND NULLIF(current_setting('app.tenant_id', true), '') IS NOT NULL
       AND NULLIF(current_setting('app.workspace_id', true), '') IS NOT NULL
       AND row_tenant = NULLIF(current_setting('app.tenant_id', true), '')
       AND row_workspace = NULLIF(current_setting('app.workspace_id', true), '')
$$;

CREATE SCHEMA IF NOT EXISTS knowledge_bits;
CREATE SCHEMA IF NOT EXISTS knowledge_bits_quarantine;
REVOKE ALL ON SCHEMA knowledge_bits_quarantine FROM PUBLIC;

DO $kb_rls$
DECLARE
    relation RECORD;
    stale_policy RECORD;
    target_name TEXT;
BEGIN
    FOR relation IN
        SELECT rel.oid AS relation_oid,
               rel.relname AS relation_name,
               EXISTS (
                   SELECT 1 FROM pg_attribute attribute
                    WHERE attribute.attrelid = rel.oid
                      AND attribute.attname = 'tenant_id'
                      AND attribute.attnum > 0
                      AND NOT attribute.attisdropped
               ) AS has_tenant,
               EXISTS (
                   SELECT 1 FROM pg_attribute attribute
                    WHERE attribute.attrelid = rel.oid
                      AND attribute.attname = 'workspace_id'
                      AND attribute.attnum > 0
                      AND NOT attribute.attisdropped
               ) AS has_workspace
          FROM pg_class rel
          JOIN pg_namespace namespace ON namespace.oid = rel.relnamespace
         WHERE namespace.nspname = 'knowledge_bits'
           AND rel.relkind = 'r'
         ORDER BY rel.relname
    LOOP
        IF relation.has_tenant AND relation.has_workspace THEN
            EXECUTE format('ALTER TABLE knowledge_bits.%I ENABLE ROW LEVEL SECURITY', relation.relation_name);
            EXECUTE format('ALTER TABLE knowledge_bits.%I FORCE ROW LEVEL SECURITY', relation.relation_name);
            FOR stale_policy IN
                SELECT policy.polname FROM pg_policy policy
                 WHERE policy.polrelid = relation.relation_oid
                   AND policy.polname <> 'kb_workspace_scope'
            LOOP
                EXECUTE format(
                    'DROP POLICY %I ON knowledge_bits.%I',
                    stale_policy.polname,
                    relation.relation_name
                );
            END LOOP;
            EXECUTE format('DROP POLICY IF EXISTS kb_workspace_scope ON knowledge_bits.%I', relation.relation_name);
            EXECUTE format(
                'CREATE POLICY kb_workspace_scope ON knowledge_bits.%I TO PUBLIC '
                'USING (omega_rls_workspace_text_matches(tenant_id::text, workspace_id::text)) '
                'WITH CHECK (omega_rls_workspace_text_matches(tenant_id::text, workspace_id::text))',
                relation.relation_name
            );
            EXECUTE format('REVOKE ALL ON TABLE knowledge_bits.%I FROM PUBLIC', relation.relation_name);
        ELSE
            target_name := relation.relation_name;
            IF to_regclass(format('knowledge_bits_quarantine.%I', target_name)) IS NOT NULL THEN
                target_name := LEFT(relation.relation_name, 40) || '_legacy_' || relation.relation_oid::TEXT;
                EXECUTE format(
                    'ALTER TABLE knowledge_bits.%I RENAME TO %I',
                    relation.relation_name,
                    target_name
                );
            END IF;
            EXECUTE format(
                'ALTER TABLE knowledge_bits.%I SET SCHEMA knowledge_bits_quarantine',
                target_name
            );
        END IF;
    END LOOP;
END
$kb_rls$;

DO $kb_roles$
DECLARE
    role_name TEXT;
BEGIN
    FOR role_name IN
        SELECT rolname FROM pg_roles
         WHERE rolname IN (
             'omega_cartridge_hubspot',
             'omega_cartridge_salesforce',
             'omega_cartridge_sap_b1',
             'omega_cartridge_sap_hcm',
             'omega_cartridge_sap_s4',
             'omega_cartridge_sap_s4hana',
             'omega_cartridge_sap_sf',
             'omega_cartridge_sap_successfactors',
             'omega_cartridge_successfactors'
         )
         ORDER BY rolname
    LOOP
        EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA knowledge_bits FROM %I', role_name);
        EXECUTE format('REVOKE ALL ON SCHEMA knowledge_bits FROM %I', role_name);
        EXECUTE format('REVOKE ALL ON SCHEMA knowledge_bits_quarantine FROM %I', role_name);
    END LOOP;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_cartridge_replicon') THEN
        REVOKE ALL ON SCHEMA knowledge_bits_quarantine FROM omega_cartridge_replicon;
    END IF;
END
$kb_roles$;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zzzzs_knowledge_bits_workspace_rls.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
