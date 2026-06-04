-- v1.45 stabilization: classify old unscoped Vault rows and restrict global
-- Vault RLS to explicit platform-only records. Tenant/workspace credentials
-- must live in rows with tenant_id/workspace_id.

CREATE TABLE IF NOT EXISTS vault_legacy_unscoped_entries (
    scope         TEXT NOT NULL,
    cartridge     TEXT NOT NULL,
    key           TEXT NOT NULL,
    reason        TEXT NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (scope, cartridge, key)
);

INSERT INTO vault_legacy_unscoped_entries (scope, cartridge, key, reason)
SELECT
    scope,
    cartridge,
    key,
    CASE
        WHEN scope = 'connections' THEN 'legacy_global_connection_requires_workspace_migration'
        ELSE 'unexpected_unscoped_vault_entry'
    END
  FROM vault_entries
 WHERE tenant_id IS NULL
   AND workspace_id IS NULL
   AND NOT (
       (scope = 'destinations' AND cartridge = 'platform')
       OR (
           scope = 'secrets'
           AND cartridge IN ('global', 'platform', 'studio', 'system', '_system')
       )
   )
ON CONFLICT (scope, cartridge, key)
DO UPDATE SET
    reason = EXCLUDED.reason,
    last_seen_at = NOW();

DO $$
BEGIN
    IF to_regclass('public.vault_entries') IS NOT NULL
       AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_vault') THEN
        DROP POLICY IF EXISTS vault_entries_global_legacy_rls ON public.vault_entries;
        DROP POLICY IF EXISTS vault_entries_platform_global_rls ON public.vault_entries;
        CREATE POLICY vault_entries_platform_global_rls ON public.vault_entries
            FOR ALL
            TO omega_vault
            USING (
                tenant_id IS NULL
                AND workspace_id IS NULL
                AND (
                    (scope = 'destinations' AND cartridge = 'platform')
                    OR (
                        scope = 'secrets'
                        AND cartridge IN ('global', 'platform', 'studio', 'system', '_system')
                    )
                )
            )
            WITH CHECK (
                tenant_id IS NULL
                AND workspace_id IS NULL
                AND (
                    (scope = 'destinations' AND cartridge = 'platform')
                    OR (
                        scope = 'secrets'
                        AND cartridge IN ('global', 'platform', 'studio', 'system', '_system')
                    )
                )
            );
    END IF;
END $$;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99i_vault_legacy_scope_classification.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
