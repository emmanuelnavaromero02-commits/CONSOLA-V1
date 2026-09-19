-- Mission 5: server-minted evidence tickets for scheduled monitor alerts.
--
-- Why this table exists at all.
--
-- Control Room only trusts a business fact when its evidence carries an HMAC
-- attestation, and the doctrine of that attestation is one line in
-- business_runtime_evidence.py: "Attest a locator taken from a row already
-- retrieved by the server." The signer must have SEEN the row.
--
-- The scheduled monitors raise their alerts through mcp-infra, which cannot
-- sign (the evidence key is console-only) and cannot read Gold. So a monitor
-- alert could never become an eligible business fact: the alert existed, the
-- fact did not, and /control-room stayed empty.
--
-- Handing mcp-infra the signing key was rejected: control_room__raise_alert
-- takes evidence_refs from its caller, so signer and author would be the same
-- process and verification would be a tautology.
--
-- What ships instead: console stays the only holder of the key AND the only
-- signer. While serving /internal/intelligence/wisdom-bits/run -- the step of
-- the monitor chain where console itself computes the numbers from Gold --
-- console signs the observation it just computed, stores the signed reference
-- HERE, and hands back a 32-hex handle. No signature and no row hash leave
-- console: not into the agent run, not into control_room_items.metadata, not
-- into a log line. (The key id is logged: it is not secret, and it is what
-- scopes a key compromise.)
--
-- Binding. The ticket is bound at mint time to the one item the monitor is
-- about to raise. Console derives item_id itself, from the agent row it reads
-- under RLS, with the same recipe mcp-infra uses to deduplicate the alert, and
-- refuses when the requested wisdom bit is not the one that agent runs. The
-- signed business observation carries that id, so the attestation cannot
-- validate any other item. A reader picks among unexpired tickets for the item
-- and agent -- the same agent run first -- using only columns console wrote,
-- and the persisted value must still match the signed one. Nothing is consumed,
-- and the Control Room read path never writes.
--
-- Authenticator. Deliberately NOT the security context alone: that is signed
-- with a symmetric key held by many containers. Minting additionally requires
-- a live scheduled-run lease proved by assert_scheduled_effect_authority
-- (99zzq), whose table only omega_console may INSERT into, and at most one
-- ticket exists per lease. Conversational agents never hold a lease: they stay
-- advisory-only.
--
-- This table is also the audit log that did not exist before: "what was
-- attested, for which item, by which run, with which key".

CREATE TABLE IF NOT EXISTS control_room_evidence_tickets (
    -- Opaque to every consumer. 32 hex chars, from secrets.token_hex(16).
    handle          TEXT PRIMARY KEY,

    tenant_id       UUID NOT NULL,
    workspace_id    UUID NOT NULL,

    -- The item this attestation can back. Derived by console, never supplied
    -- by the caller.
    item_id         TEXT NOT NULL,

    -- Lease identity, copied from the VERIFIED scheduled-effect authority.
    agent_id        UUID NOT NULL,
    agent_run_id    TEXT,
    schedule_run_id BIGINT NOT NULL,
    fencing_token   BIGINT NOT NULL,

    -- Transport identity (verified pair key) and the context source (verified
    -- signed context), kept apart on purpose: an audit needs both.
    internal_service        TEXT NOT NULL,
    security_context_source TEXT NOT NULL,

    -- What was observed, in the server's own words.
    wisdom_bit_id   TEXT NOT NULL,
    source_dataset  TEXT NOT NULL,
    source_system   TEXT NOT NULL,
    cartridge       TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    locator_field    TEXT NOT NULL,
    locator_value    TEXT NOT NULL,
    source_row_hash  TEXT NOT NULL,

    -- Which key signed it. Lets a compromise window be scoped to one key
    -- instead of retiring every attestation the platform ever issued.
    attestation_key_id TEXT NOT NULL,
    business_binding_fingerprint TEXT NOT NULL,

    -- The full signed reference. This is the secret half, and it is why no
    -- role other than omega_console gets any grant on this table.
    reference       JSONB NOT NULL,

    minted_at       TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    -- How long the ticket can back its alert. Lives on the row rather than in
    -- the signed payload, so it can change without bumping the attestation
    -- version and invalidating every reference already persisted.
    expires_at      TIMESTAMPTZ NOT NULL,

    -- RESTRICT, not CASCADE: this is an audit trail, and 45_cascade_to_restrict
    -- keeps audit rows from disappearing with their workspace.
    CONSTRAINT control_room_evidence_tickets_scope_fk
        FOREIGN KEY (tenant_id, workspace_id)
        REFERENCES workspaces(tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT control_room_evidence_tickets_handle_check
        CHECK (handle ~ '^[0-9a-f]{32}$'),
    CONSTRAINT control_room_evidence_tickets_item_check
        CHECK (item_id ~ '^agent_alert:[0-9a-f]{32}$'),
    -- The attested locator is the item itself, nothing else.
    CONSTRAINT control_room_evidence_tickets_locator_check
        CHECK (
            locator_field = 'item_id'
            AND locator_value = item_id
            AND source_record_id = 'record-' || item_id
        ),
    CONSTRAINT control_room_evidence_tickets_expiry_check
        CHECK (expires_at > minted_at),
    CONSTRAINT control_room_evidence_tickets_reference_check
        CHECK (jsonb_typeof(reference) = 'object'),
    CONSTRAINT control_room_evidence_tickets_source_check
        CHECK (security_context_source = 'agent_runner'),
    CONSTRAINT control_room_evidence_tickets_service_check
        CHECK (
            internal_service = btrim(internal_service)
            AND length(internal_service) BETWEEN 1 AND 64
        )
);

-- One ticket per scheduled-run lease: a run computes its wisdom bit once, so a
-- second mint under the same lease is refused rather than multiplied.
CREATE UNIQUE INDEX IF NOT EXISTS control_room_evidence_tickets_lease_uidx
    ON control_room_evidence_tickets (schedule_run_id, fencing_token);

-- The read path: recent tickets for one item in one scope.
CREATE INDEX IF NOT EXISTS control_room_evidence_tickets_item_idx
    ON control_room_evidence_tickets (tenant_id, workspace_id, item_id, minted_at DESC);

-- The forensic lookup: what did this key sign, in this window.
CREATE INDEX IF NOT EXISTS control_room_evidence_tickets_audit_idx
    ON control_room_evidence_tickets (attestation_key_id, minted_at DESC);

ALTER TABLE control_room_evidence_tickets ENABLE ROW LEVEL SECURITY;
ALTER TABLE control_room_evidence_tickets FORCE ROW LEVEL SECURITY;

DO $control_room_evidence_tickets_policy$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_console') THEN
        EXECUTE 'DROP POLICY IF EXISTS control_room_evidence_tickets_workspace_scope'
                ' ON control_room_evidence_tickets';
        EXECUTE
            'CREATE POLICY control_room_evidence_tickets_workspace_scope'
            ' ON control_room_evidence_tickets'
            ' FOR ALL TO omega_console'
            ' USING (omega_rls_workspace_matches(tenant_id, workspace_id))'
            ' WITH CHECK (omega_rls_workspace_matches(tenant_id, workspace_id))';
    ELSE
        RAISE NOTICE 'control_room_evidence_tickets: omega_console absent, policy skipped';
    END IF;
END
$control_room_evidence_tickets_policy$;

REVOKE ALL ON control_room_evidence_tickets FROM PUBLIC;

DO $control_room_evidence_tickets_grants$
DECLARE
    service_role TEXT;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_console') THEN
        -- Append-only. No UPDATE: a stored reference, locator or key id can
        -- never be rewritten. No DELETE and no TRUNCATE: expired tickets stay
        -- as the record of what was minted.
        EXECUTE 'REVOKE ALL ON control_room_evidence_tickets FROM omega_console';
        EXECUTE 'GRANT SELECT, INSERT ON control_room_evidence_tickets TO omega_console';
    END IF;

    -- Every other service role is explicitly stripped, so a future broad
    -- grant cannot silently hand mcp-infra (or anyone else) the signed half.
    FOREACH service_role IN ARRAY ARRAY[
        'omega_mcp_infra', 'omega_refinement', 'omega_vault', 'omega_workspace'
    ]
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = service_role) THEN
            EXECUTE format(
                'REVOKE ALL ON control_room_evidence_tickets FROM %I',
                service_role
            );
        END IF;
    END LOOP;
END
$control_room_evidence_tickets_grants$;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zzzzi_control_room_evidence_tickets.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
