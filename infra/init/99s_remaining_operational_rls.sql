-- Prompt 17.6: close remaining operational platform-owner RLS policies.
--
-- This migration is intentionally surgical: it removes unconditional
-- platform-owner access from tenant/workspace operational tables that have
-- confirmed runtime consumers, while leaving auth/provisioning bootstrap
-- exceptions to the explicit allowlist introduced in 99m.

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
    role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY[
        'omega_console',
        'omega_refinement',
        'omega_workspace',
        'omega_mcp_infra',
        'omega_vault',
        'omega_airflow_dag'
    ]
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format('ALTER ROLE %I NOBYPASSRLS', role_name);
        END IF;
    END LOOP;
END $$;

DO $$
DECLARE
    owner_roles text;
    tbl text;
    tenant_expr text;
    direct_tables CONSTANT text[] := ARRAY[
        'action_runs',
        'action_run_events',
        'conversations',
        'intelligence_runs',
        'decision_intelligence_snapshots',
        'backtest_runs',
        'backtest_results',
        'agent_runs',
        'vault_entries',
        'vault_access_log'
    ];
BEGIN
    SELECT string_agg(quote_ident(rolname), ', ')
      INTO owner_roles
      FROM pg_roles
     WHERE rolname = ANY(ARRAY['omega_console', 'omega_refinement']);

    IF owner_roles IS NULL THEN
        RETURN;
    END IF;

    FOREACH tbl IN ARRAY direct_tables
    LOOP
        IF to_regclass('public.' || tbl) IS NULL THEN
            CONTINUE;
        END IF;

        IF NOT EXISTS (
            SELECT 1
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = tbl
               AND column_name = 'workspace_id'
        ) THEN
            RAISE EXCEPTION 'operational table %.% has no workspace_id; refusing owner RLS close', 'public', tbl;
        END IF;

        IF EXISTS (
            SELECT 1
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = tbl
               AND column_name = 'tenant_id'
        ) THEN
            tenant_expr := 'tenant_id';
        ELSE
            tenant_expr := 'NULL::uuid';
        END IF;

        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', tbl);
        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', tbl || '_platform_owner_rls', tbl);
        EXECUTE format('DROP POLICY IF EXISTS console_refinement_scope_rls ON public.%I', tbl);

        EXECUTE format(
            'CREATE POLICY console_refinement_scope_rls ON public.%I
               FOR ALL
               TO %s
               USING (omega_rls_workspace_matches(%s, workspace_id))
               WITH CHECK (omega_rls_workspace_matches(%s, workspace_id))',
            tbl,
            owner_roles,
            tenant_expr,
            tenant_expr
        );
    END LOOP;
END $$;

DO $$
DECLARE
    owner_roles text;
BEGIN
    SELECT string_agg(quote_ident(rolname), ', ')
      INTO owner_roles
      FROM pg_roles
     WHERE rolname = ANY(ARRAY['omega_console', 'omega_refinement']);

    IF owner_roles IS NULL THEN
        RETURN;
    END IF;

    IF to_regclass('public.decision_actions') IS NOT NULL THEN
        ALTER TABLE public.decision_actions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.decision_actions FORCE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS decision_actions_platform_owner_rls ON public.decision_actions;
        DROP POLICY IF EXISTS decision_actions_console_refinement_scope_rls ON public.decision_actions;
        EXECUTE format(
            'CREATE POLICY decision_actions_console_refinement_scope_rls ON public.decision_actions
               FOR ALL
               TO %s
               USING (
                   EXISTS (
                       SELECT 1
                         FROM public.decisions d
                        WHERE d.id = decision_actions.decision_id
                          AND omega_rls_workspace_matches(NULL::uuid, d.workspace_id)
                   )
               )
               WITH CHECK (
                   EXISTS (
                       SELECT 1
                         FROM public.decisions d
                        WHERE d.id = decision_actions.decision_id
                          AND omega_rls_workspace_matches(NULL::uuid, d.workspace_id)
                   )
               )',
            owner_roles
        );
    END IF;

    IF to_regclass('public.conversation_messages') IS NOT NULL THEN
        ALTER TABLE public.conversation_messages ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.conversation_messages FORCE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS conversation_messages_platform_owner_rls ON public.conversation_messages;
        DROP POLICY IF EXISTS conversation_messages_console_refinement_scope_rls ON public.conversation_messages;
        EXECUTE format(
            'CREATE POLICY conversation_messages_console_refinement_scope_rls ON public.conversation_messages
               FOR ALL
               TO %s
               USING (
                   EXISTS (
                       SELECT 1
                         FROM public.conversations c
                        WHERE c.id = conversation_messages.conversation_id
                          AND omega_rls_workspace_matches(NULL::uuid, c.workspace_id)
                   )
               )
               WITH CHECK (
                   EXISTS (
                       SELECT 1
                         FROM public.conversations c
                        WHERE c.id = conversation_messages.conversation_id
                          AND omega_rls_workspace_matches(NULL::uuid, c.workspace_id)
                   )
               )',
            owner_roles
        );
    END IF;

    IF to_regclass('public.cartridge_installation_events') IS NOT NULL THEN
        ALTER TABLE public.cartridge_installation_events ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.cartridge_installation_events FORCE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS cartridge_installation_events_platform_owner_rls ON public.cartridge_installation_events;
        DROP POLICY IF EXISTS cartridge_installation_events_console_refinement_scope_rls ON public.cartridge_installation_events;
        EXECUTE format(
            'CREATE POLICY cartridge_installation_events_console_refinement_scope_rls ON public.cartridge_installation_events
               FOR ALL
               TO %s
               USING (
                   EXISTS (
                       SELECT 1
                         FROM public.cartridge_installations ci
                        WHERE ci.id = cartridge_installation_events.installation_id
                          AND omega_rls_workspace_matches(ci.tenant_id, ci.workspace_id)
                   )
               )
               WITH CHECK (
                   EXISTS (
                       SELECT 1
                         FROM public.cartridge_installations ci
                        WHERE ci.id = cartridge_installation_events.installation_id
                          AND omega_rls_workspace_matches(ci.tenant_id, ci.workspace_id)
                   )
               )',
            owner_roles
        );
    END IF;
END $$;

DO $$
DECLARE
    owner_roles text;
BEGIN
    SELECT string_agg(quote_ident(rolname), ', ')
      INTO owner_roles
      FROM pg_roles
     WHERE rolname = ANY(ARRAY['omega_console', 'omega_refinement']);

    IF owner_roles IS NULL OR to_regclass('public.agents') IS NULL THEN
        RETURN;
    END IF;

    ALTER TABLE public.agents ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.agents FORCE ROW LEVEL SECURITY;

    DROP POLICY IF EXISTS agents_platform_owner_rls ON public.agents;
    DROP POLICY IF EXISTS agents_console_refinement_scope_rls ON public.agents;
    DROP POLICY IF EXISTS agents_console_refinement_global_template_rls ON public.agents;
    DROP POLICY IF EXISTS agents_console_refinement_scheduled_read_rls ON public.agents;

    EXECUTE format(
        'CREATE POLICY agents_console_refinement_scope_rls ON public.agents
           FOR ALL
           TO %s
           USING (omega_rls_workspace_matches(tenant_id, workspace_id))
           WITH CHECK (omega_rls_workspace_matches(tenant_id, workspace_id))',
        owner_roles
    );

    EXECUTE format(
        'CREATE POLICY agents_console_refinement_global_template_rls ON public.agents
           FOR ALL
           TO %s
           USING (workspace_id IS NULL)
           WITH CHECK (workspace_id IS NULL)',
        owner_roles
    );

    EXECUTE format(
        'CREATE POLICY agents_console_refinement_scheduled_read_rls ON public.agents
           FOR SELECT
           TO %s
           USING (
               omega_rls_workspace_matches(tenant_id, workspace_id)
               AND lower(coalesce(extra #>> ''{schedule,enabled}'', ''false'')) IN (''true'', ''1'', ''yes'', ''on'')
           )',
        owner_roles
    );
END $$;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99s_remaining_operational_rls.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
