-- P1.2c3 PR-A: scoped RLS, immutable ledger and one-way run linkage.

CREATE OR REPLACE FUNCTION reject_control_room_intent_event_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'control room action intent events are append-only'
      USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS control_room_intent_events_no_mutation
    ON control_room_action_intent_events;
CREATE TRIGGER control_room_intent_events_no_mutation
BEFORE UPDATE OR DELETE ON control_room_action_intent_events
FOR EACH ROW EXECUTE FUNCTION reject_control_room_intent_event_mutation();

DROP TRIGGER IF EXISTS control_room_intent_events_no_truncate
    ON control_room_action_intent_events;
CREATE TRIGGER control_room_intent_events_no_truncate
BEFORE TRUNCATE ON control_room_action_intent_events
FOR EACH STATEMENT EXECUTE FUNCTION reject_control_room_intent_event_mutation();

CREATE OR REPLACE FUNCTION protect_action_run_intent_link()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.action_intent_id IS NOT NULL
       AND NEW.action_intent_id IS DISTINCT FROM OLD.action_intent_id THEN
        RAISE EXCEPTION 'action run intent link is immutable'
          USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS action_runs_intent_link_immutable ON action_runs;
CREATE TRIGGER action_runs_intent_link_immutable
BEFORE UPDATE OF action_intent_id ON action_runs
FOR EACH ROW EXECUTE FUNCTION protect_action_run_intent_link();

CREATE OR REPLACE FUNCTION protect_terminal_control_room_intent()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.state IN ('rejected', 'stale', 'completed', 'failed')
       AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'terminal control room action intent is immutable'
          USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS control_room_action_intents_terminal_immutable
    ON control_room_action_intents;
CREATE TRIGGER control_room_action_intents_terminal_immutable
BEFORE UPDATE ON control_room_action_intents
FOR EACH ROW EXECUTE FUNCTION protect_terminal_control_room_intent();

DO $$
DECLARE
    tbl TEXT;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_console') THEN
        ALTER ROLE omega_console NOBYPASSRLS;
    END IF;
    FOREACH tbl IN ARRAY ARRAY[
        'control_room_action_intents',
        'control_room_action_tokens',
        'control_room_action_intent_events'
    ] LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', tbl);
        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', tbl || '_console_select', tbl);
        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', tbl || '_console_insert', tbl);
        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', tbl || '_console_update', tbl);
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_console') THEN
            EXECUTE format(
                'CREATE POLICY %I ON public.%I FOR SELECT TO omega_console '
                'USING (omega_rls_workspace_matches(tenant_id, workspace_id))',
                tbl || '_console_select', tbl
            );
            EXECUTE format(
                'CREATE POLICY %I ON public.%I FOR INSERT TO omega_console '
                'WITH CHECK (omega_rls_workspace_matches(tenant_id, workspace_id))',
                tbl || '_console_insert', tbl
            );
            IF tbl <> 'control_room_action_intent_events' THEN
                EXECUTE format(
                    'CREATE POLICY %I ON public.%I FOR UPDATE TO omega_console '
                    'USING (omega_rls_workspace_matches(tenant_id, workspace_id)) '
                    'WITH CHECK (omega_rls_workspace_matches(tenant_id, workspace_id))',
                    tbl || '_console_update', tbl
                );
            END IF;
        END IF;
    END LOOP;
END $$;

REVOKE ALL ON control_room_action_intents FROM PUBLIC;
REVOKE ALL ON control_room_action_tokens FROM PUBLIC;
REVOKE ALL ON control_room_action_intent_events FROM PUBLIC;
REVOKE ALL ON control_room_action_intents FROM omega_console;
REVOKE ALL ON control_room_action_tokens FROM omega_console;
REVOKE ALL ON control_room_action_intent_events FROM omega_console;

GRANT SELECT, INSERT ON control_room_action_intents TO omega_console;
GRANT UPDATE (
    checker_user_id, executor_user_id, state, state_version,
    result_code, updated_at
) ON control_room_action_intents TO omega_console;
GRANT SELECT, INSERT ON control_room_action_tokens TO omega_console;
GRANT UPDATE (
    status, operation_digest, result_state, result_version,
    consumed_at, consumed_by, token_digest, issued_at, expires_at,
    item_id, template_id, binding_digest, evidence_digest,
    observation_fingerprint, contract_digest, target_digest,
    decision_digest, intent_id, binding_dry_run_action_run_id,
    binding_dry_run_evidence_digest
) ON control_room_action_tokens TO omega_console;
GRANT SELECT, INSERT ON control_room_action_intent_events TO omega_console;
GRANT USAGE, SELECT ON SEQUENCE control_room_action_intent_events_id_seq
    TO omega_console;

REVOKE UPDATE, DELETE, TRUNCATE ON control_room_action_intent_events
    FROM omega_console;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zzc_control_room_action_authority_security.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
