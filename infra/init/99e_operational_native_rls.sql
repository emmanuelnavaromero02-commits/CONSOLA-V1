-- v1.45.3: Native Postgres RLS for tenant/workspace operational tables.
--
-- This is the second line of defense behind the application guards. Console
-- remains the platform owner role while request-scoped DB context is rolled out
-- everywhere; tenant-facing/service roles must provide app.tenant_id and
-- app.workspace_id or they see no workspace-scoped rows.

CREATE OR REPLACE FUNCTION omega_rls_workspace_matches(row_tenant uuid, row_workspace uuid)
RETURNS boolean
LANGUAGE sql
STABLE
AS $$
    SELECT
        row_workspace IS NOT NULL
        AND NULLIF(current_setting('app.workspace_id', true), '') IS NOT NULL
        AND row_workspace::text = NULLIF(current_setting('app.workspace_id', true), '')
        AND (
            row_tenant IS NULL
            OR (
                NULLIF(current_setting('app.tenant_id', true), '') IS NOT NULL
                AND row_tenant::text = NULLIF(current_setting('app.tenant_id', true), '')
            )
        )
$$;

DO $$
DECLARE
    scoped_roles text;
    owner_roles text;
    tbl text;
    tenant_expr text;
    scope_expr text;
    scoped_tables CONSTANT text[] := ARRAY[
        'datasets',
        'decisions',
        'control_room_items',
        'control_room_item_events',
        'control_room_action_executions',
        'control_room_thresholds',
        'control_room_lessons',
        'token_usage',
        'vault_entries',
        'vault_access_log',
        'agents',
        'agent_runs',
        'conversations',
        'user_cartridge_overrides',
        'marketplace_orders',
        'tenant_entitlements',
        'cartridge_installations'
    ];
BEGIN
    SELECT string_agg(quote_ident(rolname), ', ')
      INTO scoped_roles
      FROM pg_roles
     WHERE rolname = ANY(ARRAY[
        'omega_workspace',
        'omega_mcp_infra',
        'omega_vault',
        'omega_cartridge_replicon',
        'omega_cartridge_hubspot',
        'omega_cartridge_salesforce',
        'omega_cartridge_sap_hcm',
        'omega_cartridge_sap_s4',
        'omega_cartridge_sap_sf',
        'omega_airflow_dag'
     ]);

    SELECT string_agg(quote_ident(rolname), ', ')
      INTO owner_roles
      FROM pg_roles
     WHERE rolname = ANY(ARRAY[
        'omega_console',
        'omega_refinement'
     ]);

    FOREACH tbl IN ARRAY scoped_tables
    LOOP
        IF NOT EXISTS (
            SELECT 1
              FROM information_schema.tables ist
             WHERE ist.table_schema = 'public'
               AND ist.table_name = tbl
        ) THEN
            CONTINUE;
        END IF;

        IF NOT EXISTS (
            SELECT 1
              FROM information_schema.columns isc
             WHERE isc.table_schema = 'public'
               AND isc.table_name = tbl
               AND isc.column_name = 'workspace_id'
        ) THEN
            CONTINUE;
        END IF;

        IF EXISTS (
            SELECT 1
              FROM information_schema.columns isc
             WHERE isc.table_schema = 'public'
               AND isc.table_name = tbl
               AND isc.column_name = 'tenant_id'
        ) THEN
            tenant_expr := 'tenant_id';
        ELSE
            tenant_expr := 'NULL::uuid';
        END IF;

        scope_expr := format(
            'omega_rls_workspace_matches(%s, workspace_id)',
            tenant_expr
        );

        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', tbl);

        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', tbl || '_tenant_workspace_rls', tbl);
        IF scoped_roles IS NOT NULL THEN
            EXECUTE format(
                'CREATE POLICY %I ON public.%I
                   FOR ALL
                   TO %s
                   USING (%s)
                   WITH CHECK (%s)',
                tbl || '_tenant_workspace_rls',
                tbl,
                scoped_roles,
                scope_expr,
                scope_expr
            );
        END IF;

        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', tbl || '_platform_owner_rls', tbl);
        IF owner_roles IS NOT NULL THEN
            EXECUTE format(
                'CREATE POLICY %I ON public.%I
                   FOR ALL
                   TO %s
                   USING (true)
                   WITH CHECK (true)',
                tbl || '_platform_owner_rls',
                tbl,
                owner_roles
            );
        END IF;
    END LOOP;
END $$;

DO $$
DECLARE
    scoped_roles text;
    owner_roles text;
BEGIN
    SELECT string_agg(quote_ident(rolname), ', ')
      INTO scoped_roles
      FROM pg_roles
     WHERE rolname = ANY(ARRAY['omega_workspace', 'omega_console']);

    SELECT string_agg(quote_ident(rolname), ', ')
      INTO owner_roles
      FROM pg_roles
     WHERE rolname = ANY(ARRAY['omega_console']);

    IF to_regclass('public.decision_actions') IS NOT NULL THEN
        ALTER TABLE public.decision_actions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.decision_actions FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS decision_actions_tenant_workspace_rls ON public.decision_actions;
        IF scoped_roles IS NOT NULL THEN
            EXECUTE format(
                'CREATE POLICY decision_actions_tenant_workspace_rls ON public.decision_actions
                   FOR ALL
                   TO %s
                   USING (
                       EXISTS (
                           SELECT 1 FROM public.decisions d
                            WHERE d.id = decision_actions.decision_id
                              AND omega_rls_workspace_matches(NULL::uuid, d.workspace_id)
                       )
                   )
                   WITH CHECK (
                       EXISTS (
                           SELECT 1 FROM public.decisions d
                            WHERE d.id = decision_actions.decision_id
                              AND omega_rls_workspace_matches(NULL::uuid, d.workspace_id)
                       )
                   )',
                scoped_roles
            );
        END IF;

        DROP POLICY IF EXISTS decision_actions_platform_owner_rls ON public.decision_actions;
        IF owner_roles IS NOT NULL THEN
            EXECUTE format(
                'CREATE POLICY decision_actions_platform_owner_rls ON public.decision_actions
                   FOR ALL
                   TO %s
                   USING (true)
                   WITH CHECK (true)',
                owner_roles
            );
        END IF;
    END IF;

    IF to_regclass('public.conversation_messages') IS NOT NULL THEN
        ALTER TABLE public.conversation_messages ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.conversation_messages FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS conversation_messages_workspace_rls ON public.conversation_messages;
        IF scoped_roles IS NOT NULL THEN
            EXECUTE format(
                'CREATE POLICY conversation_messages_workspace_rls ON public.conversation_messages
                   FOR ALL
                   TO %s
                   USING (
                       EXISTS (
                           SELECT 1 FROM public.conversations c
                            WHERE c.id = conversation_messages.conversation_id
                              AND omega_rls_workspace_matches(NULL::uuid, c.workspace_id)
                       )
                   )
                   WITH CHECK (
                       EXISTS (
                           SELECT 1 FROM public.conversations c
                            WHERE c.id = conversation_messages.conversation_id
                              AND omega_rls_workspace_matches(NULL::uuid, c.workspace_id)
                       )
                   )',
                scoped_roles
            );
        END IF;

        DROP POLICY IF EXISTS conversation_messages_platform_owner_rls ON public.conversation_messages;
        IF owner_roles IS NOT NULL THEN
            EXECUTE format(
                'CREATE POLICY conversation_messages_platform_owner_rls ON public.conversation_messages
                   FOR ALL
                   TO %s
                   USING (true)
                   WITH CHECK (true)',
                owner_roles
            );
        END IF;
    END IF;

    IF to_regclass('public.cartridge_installation_events') IS NOT NULL THEN
        ALTER TABLE public.cartridge_installation_events ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.cartridge_installation_events FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS cartridge_installation_events_workspace_rls ON public.cartridge_installation_events;
        IF scoped_roles IS NOT NULL THEN
            EXECUTE format(
                'CREATE POLICY cartridge_installation_events_workspace_rls ON public.cartridge_installation_events
                   FOR ALL
                   TO %s
                   USING (
                       EXISTS (
                           SELECT 1 FROM public.cartridge_installations ci
                            WHERE ci.id = cartridge_installation_events.installation_id
                              AND omega_rls_workspace_matches(ci.tenant_id, ci.workspace_id)
                       )
                   )
                   WITH CHECK (
                       EXISTS (
                           SELECT 1 FROM public.cartridge_installations ci
                            WHERE ci.id = cartridge_installation_events.installation_id
                              AND omega_rls_workspace_matches(ci.tenant_id, ci.workspace_id)
                       )
                   )',
                scoped_roles
            );
        END IF;

        DROP POLICY IF EXISTS cartridge_installation_events_platform_owner_rls ON public.cartridge_installation_events;
        IF owner_roles IS NOT NULL THEN
            EXECUTE format(
                'CREATE POLICY cartridge_installation_events_platform_owner_rls ON public.cartridge_installation_events
                   FOR ALL
                   TO %s
                   USING (true)
                   WITH CHECK (true)',
                owner_roles
            );
        END IF;
    END IF;
END $$;

DO $$
DECLARE
    agent_read_roles text;
BEGIN
    SELECT string_agg(quote_ident(rolname), ', ')
      INTO agent_read_roles
      FROM pg_roles
     WHERE rolname = ANY(ARRAY['omega_workspace', 'omega_mcp_infra']);

    IF to_regclass('public.agents') IS NOT NULL AND agent_read_roles IS NOT NULL THEN
        DROP POLICY IF EXISTS agents_global_template_read_rls ON public.agents;
        EXECUTE format(
            'CREATE POLICY agents_global_template_read_rls ON public.agents
               FOR SELECT
               TO %s
               USING (workspace_id IS NULL)',
            agent_read_roles
        );
    END IF;

    IF to_regclass('public.vault_entries') IS NOT NULL
       AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_vault') THEN
        DROP POLICY IF EXISTS vault_entries_global_legacy_rls ON public.vault_entries;
        CREATE POLICY vault_entries_global_legacy_rls ON public.vault_entries
            FOR ALL
            TO omega_vault
            USING (tenant_id IS NULL AND workspace_id IS NULL)
            WITH CHECK (tenant_id IS NULL AND workspace_id IS NULL);
    END IF;

    IF to_regclass('public.vault_access_log') IS NOT NULL
       AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_vault') THEN
        DROP POLICY IF EXISTS vault_access_log_global_legacy_rls ON public.vault_access_log;
        CREATE POLICY vault_access_log_global_legacy_rls ON public.vault_access_log
            FOR ALL
            TO omega_vault
            USING (tenant_id IS NULL AND workspace_id IS NULL)
            WITH CHECK (tenant_id IS NULL AND workspace_id IS NULL);
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_mcp_infra') THEN
        ALTER ROLE omega_mcp_infra NOBYPASSRLS;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_workspace') THEN
        ALTER ROLE omega_workspace NOBYPASSRLS;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_vault') THEN
        ALTER ROLE omega_vault NOBYPASSRLS;
    END IF;
END $$;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99e_operational_native_rls.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
