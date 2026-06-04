-- v1.45 stabilization: scoped Vault rows, intelligence ownership, and
-- legacy Vault backfill from the old tenant/workspace name prefix.

ALTER TABLE vault_entries
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;

ALTER TABLE vault_entries
    ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces(id) ON DELETE CASCADE;

ALTER TABLE vault_access_log
    ADD COLUMN IF NOT EXISTS tenant_id UUID;

ALTER TABLE vault_access_log
    ADD COLUMN IF NOT EXISTS workspace_id UUID;

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'intelligence_signals',
        'evidence_packs',
        'evidence_items',
        'hypotheses',
        'decision_options',
        'prediction_outcomes',
        'control_room_items'
    ]
    LOOP
        IF to_regclass('public.' || t) IS NOT NULL THEN
            EXECUTE format(
                'ALTER TABLE public.%I ADD COLUMN IF NOT EXISTS owner_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL',
                t
            );
            EXECUTE format(
                'CREATE INDEX IF NOT EXISTS %I ON public.%I(workspace_id, owner_user_id)',
                t || '_workspace_owner_idx',
                t
            );
        END IF;
    END LOOP;
END $$;

DO $$
BEGIN
    IF to_regclass('public.vault_entries') IS NOT NULL
       AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_vault') THEN
        ALTER TABLE public.vault_entries ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.vault_entries FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS vault_entries_tenant_workspace_rls ON public.vault_entries;
        CREATE POLICY vault_entries_tenant_workspace_rls ON public.vault_entries
            FOR ALL
            TO omega_vault
            USING (
                tenant_id IS NOT NULL
                AND workspace_id IS NOT NULL
                AND workspace_id::text = NULLIF(current_setting('app.workspace_id', true), '')
                AND tenant_id::text = NULLIF(current_setting('app.tenant_id', true), '')
            )
            WITH CHECK (
                tenant_id IS NOT NULL
                AND workspace_id IS NOT NULL
                AND workspace_id::text = NULLIF(current_setting('app.workspace_id', true), '')
                AND tenant_id::text = NULLIF(current_setting('app.tenant_id', true), '')
            );
    END IF;

    IF to_regclass('public.vault_access_log') IS NOT NULL
       AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_vault') THEN
        ALTER TABLE public.vault_access_log ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.vault_access_log FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS vault_access_log_tenant_workspace_rls ON public.vault_access_log;
        CREATE POLICY vault_access_log_tenant_workspace_rls ON public.vault_access_log
            FOR ALL
            TO omega_vault
            USING (
                tenant_id IS NOT NULL
                AND workspace_id IS NOT NULL
                AND workspace_id::text = NULLIF(current_setting('app.workspace_id', true), '')
                AND tenant_id::text = NULLIF(current_setting('app.tenant_id', true), '')
            )
            WITH CHECK (
                tenant_id IS NOT NULL
                AND workspace_id IS NOT NULL
                AND workspace_id::text = NULLIF(current_setting('app.workspace_id', true), '')
                AND tenant_id::text = NULLIF(current_setting('app.tenant_id', true), '')
            );
    END IF;
END $$;

WITH parsed AS (
    SELECT
        ctid,
        scope,
        key,
        (regexp_match(cartridge, '^tenant_([0-9a-fA-F-]{36})__workspace_([0-9a-fA-F-]{36})__(.+)$')) AS m
    FROM vault_entries
    WHERE tenant_id IS NULL
      AND workspace_id IS NULL
      AND cartridge ~ '^tenant_[0-9a-fA-F-]{36}__workspace_[0-9a-fA-F-]{36}__'
),
valid AS (
    SELECT p.ctid, p.m[1]::uuid AS tenant_id, p.m[2]::uuid AS workspace_id, p.m[3] AS clean_cartridge
    FROM parsed p
    JOIN tenants t ON t.id = p.m[1]::uuid
    JOIN workspaces w ON w.id = p.m[2]::uuid AND w.tenant_id = t.id
    WHERE p.m IS NOT NULL
      AND NOT EXISTS (
          SELECT 1
            FROM vault_entries existing
           WHERE existing.tenant_id = p.m[1]::uuid
             AND existing.workspace_id = p.m[2]::uuid
             AND existing.scope = p.scope
             AND existing.cartridge = p.m[3]
             AND existing.key = p.key
      )
)
UPDATE vault_entries v
   SET tenant_id = valid.tenant_id,
       workspace_id = valid.workspace_id,
       cartridge = valid.clean_cartridge
  FROM valid
 WHERE v.ctid = valid.ctid;

WITH parsed AS (
    SELECT
        ctid,
        scope,
        cartridge,
        (regexp_match(key, '^tenant_([0-9a-fA-F-]{36})__workspace_([0-9a-fA-F-]{36})__(.+)$')) AS m
    FROM vault_entries
    WHERE tenant_id IS NULL
      AND workspace_id IS NULL
      AND key ~ '^tenant_[0-9a-fA-F-]{36}__workspace_[0-9a-fA-F-]{36}__'
),
valid AS (
    SELECT p.ctid, p.m[1]::uuid AS tenant_id, p.m[2]::uuid AS workspace_id, p.m[3] AS clean_key
    FROM parsed p
    JOIN tenants t ON t.id = p.m[1]::uuid
    JOIN workspaces w ON w.id = p.m[2]::uuid AND w.tenant_id = t.id
    WHERE p.m IS NOT NULL
      AND NOT EXISTS (
          SELECT 1
            FROM vault_entries existing
           WHERE existing.tenant_id = p.m[1]::uuid
             AND existing.workspace_id = p.m[2]::uuid
             AND existing.scope = p.scope
             AND existing.cartridge = p.cartridge
             AND existing.key = p.m[3]
      )
)
UPDATE vault_entries v
   SET tenant_id = valid.tenant_id,
       workspace_id = valid.workspace_id,
       key = valid.clean_key
  FROM valid
 WHERE v.ctid = valid.ctid;

WITH parsed AS (
    SELECT
        ctid,
        cartridge,
        key,
        (regexp_match(scope, '^tenant_([0-9a-fA-F-]{36})__workspace_([0-9a-fA-F-]{36})__(.+)$')) AS m
    FROM vault_entries
    WHERE tenant_id IS NULL
      AND workspace_id IS NULL
      AND scope ~ '^tenant_[0-9a-fA-F-]{36}__workspace_[0-9a-fA-F-]{36}__'
),
valid AS (
    SELECT p.ctid, p.m[1]::uuid AS tenant_id, p.m[2]::uuid AS workspace_id, p.m[3] AS clean_scope
    FROM parsed p
    JOIN tenants t ON t.id = p.m[1]::uuid
    JOIN workspaces w ON w.id = p.m[2]::uuid AND w.tenant_id = t.id
    WHERE p.m IS NOT NULL
      AND NOT EXISTS (
          SELECT 1
            FROM vault_entries existing
           WHERE existing.tenant_id = p.m[1]::uuid
             AND existing.workspace_id = p.m[2]::uuid
             AND existing.scope = p.m[3]
             AND existing.cartridge = p.cartridge
             AND existing.key = p.key
      )
)
UPDATE vault_entries v
   SET tenant_id = valid.tenant_id,
       workspace_id = valid.workspace_id,
       scope = valid.clean_scope
  FROM valid
 WHERE v.ctid = valid.ctid;

CREATE INDEX IF NOT EXISTS idx_vault_entries_workspace_scope_lookup
    ON vault_entries(workspace_id, scope, cartridge, key);

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99g_scope_owner_hotfix.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
