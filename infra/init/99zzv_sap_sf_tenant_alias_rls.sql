-- 99zzv_sap_sf_tenant_alias_rls.sql
--
-- F3-5: enrol sap_successfactors_tenant_entity_aliases in row level security.
-- The table is tenant/workspace scoped but shipped without any policy
-- (99zp), so any role with table access could read every tenant's alias
-- mappings. Pattern copied from 99n/99w: ENABLE + FORCE ROW LEVEL SECURITY,
-- scoped policy for the SF cartridge role via omega_rls_workspace_matches,
-- owner passthrough for omega_console. Platform-global rows
-- (tenant_id IS NULL AND workspace_id IS NULL) stay readable by any scoped
-- session: they are the documented fallback of the preflight alias reader.

DO $$
DECLARE
    scoped_roles text;
    owner_roles text;
BEGIN
    IF to_regclass('public.sap_successfactors_tenant_entity_aliases') IS NULL THEN
        RETURN;
    END IF;

    SELECT string_agg(quote_ident(rolname), ', ')
      INTO scoped_roles
      FROM pg_roles
     WHERE rolname = 'omega_cartridge_sap_sf';

    SELECT string_agg(quote_ident(rolname), ', ')
      INTO owner_roles
      FROM pg_roles
     WHERE rolname = ANY(ARRAY['omega_console']);

    ALTER TABLE sap_successfactors_tenant_entity_aliases ENABLE ROW LEVEL SECURITY;
    ALTER TABLE sap_successfactors_tenant_entity_aliases FORCE ROW LEVEL SECURITY;

    -- 99w revoked inherited console DML and requires explicit grants for new
    -- tables; 99zp shipped without any, so fresh installs could never read the
    -- aliases. Grant the minimum: read for the cartridge, management for console.
    IF scoped_roles IS NOT NULL THEN
        EXECUTE format(
            'GRANT SELECT ON sap_successfactors_tenant_entity_aliases TO %s',
            scoped_roles
        );
    END IF;
    IF owner_roles IS NOT NULL THEN
        EXECUTE format(
            'GRANT SELECT, INSERT, UPDATE, DELETE ON sap_successfactors_tenant_entity_aliases TO %s',
            owner_roles
        );
    END IF;

    DROP POLICY IF EXISTS sap_sf_tenant_alias_workspace_rls ON sap_successfactors_tenant_entity_aliases;
    IF scoped_roles IS NOT NULL THEN
        EXECUTE format(
            'CREATE POLICY sap_sf_tenant_alias_workspace_rls ON sap_successfactors_tenant_entity_aliases
               FOR ALL TO %s
               USING (
                   (tenant_id IS NULL AND workspace_id IS NULL)
                   OR omega_rls_workspace_matches(tenant_id, workspace_id)
               )
               WITH CHECK (
                   omega_rls_workspace_matches(tenant_id, workspace_id)
               )',
            scoped_roles
        );
    END IF;

    DROP POLICY IF EXISTS sap_sf_tenant_alias_platform_owner_rls ON sap_successfactors_tenant_entity_aliases;
    IF owner_roles IS NOT NULL THEN
        EXECUTE format(
            'CREATE POLICY sap_sf_tenant_alias_platform_owner_rls ON sap_successfactors_tenant_entity_aliases
               FOR ALL TO %s USING (true) WITH CHECK (true)',
            owner_roles
        );
    END IF;
END $$;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zzv_sap_sf_tenant_alias_rls.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
