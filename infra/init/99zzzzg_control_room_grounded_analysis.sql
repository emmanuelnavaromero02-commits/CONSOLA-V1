-- Durable, claim-level grounding for Control Room generative analysis.
--
-- An analysis is a versioned handoff bound to one immutable evidence pack.
-- Only server-verified handoffs may be projected to users.  Once verified,
-- neither the handoff nor any of its claims can be changed; corrections create
-- a new version.  The LLM is never an authority for observed/computed values.

ALTER TABLE rag_sources
    ADD COLUMN IF NOT EXISTS cartridge_id TEXT REFERENCES cartridges(id) ON DELETE RESTRICT;

ALTER TABLE evidence_packs
    ADD COLUMN IF NOT EXISTS attestation_key_id TEXT,
    ADD COLUMN IF NOT EXISTS attestation_signature CHAR(64),
    ADD COLUMN IF NOT EXISTS attestation_digest CHAR(64),
    ADD COLUMN IF NOT EXISTS sealed_at TIMESTAMPTZ;

ALTER TABLE evidence_packs
    DROP CONSTRAINT IF EXISTS evidence_packs_attestation_check;
ALTER TABLE evidence_packs
    ADD CONSTRAINT evidence_packs_attestation_check CHECK (
        (sealed_at IS NULL AND attestation_key_id IS NULL
         AND attestation_signature IS NULL AND attestation_digest IS NULL)
        OR
        (sealed_at IS NOT NULL AND length(attestation_key_id) > 0
         AND attestation_signature ~ '^[0-9a-f]{64}$'
         AND attestation_digest ~ '^[0-9a-f]{64}$')
    );

-- Never infer authority from a legacy source name.  Only the server-managed
-- ingestion path may assign ``cartridge_id`` while it writes the source.  A
-- row that merely looks like ``raw:sap_successfactors:*``, ``dataset:*`` or
-- ``_semantic_*`` remains unclassified (NULL) and is therefore excluded by an
-- agent's explicit cartridge filter.

CREATE INDEX IF NOT EXISTS rag_sources_scope_cartridge_kind_idx
    ON rag_sources (tenant_id, workspace_id, cartridge_id, kind, created_at DESC);

-- The Talent monitor may reason over scoped aggregate views and cartridge-
-- filtered RAG only.  Existing rows are repaired in-place before the new
-- runtime starts; probabilistic engines remain represented for auditability
-- but cannot execute.
UPDATE agents agent
       SET allowed_tools = '[
           "mcp-infra__wisdom_bits__run",
           "mcp-infra__decision__orchestrate",
           "mcp-infra__control_room__talent_kpis_read",
           "mcp-infra__control_room__talent_overview_read",
           "mcp-infra__control_room__talent_9box_read",
           "mcp-infra__control_room__talent_metadata_readiness_read",
           "mcp-infra__search_rag",
           "mcp-infra__list_rag_sources",
           "refinement__get_schema"
       ]'::jsonb,
       rag_filter = '{"cartridges":["sap_successfactors"],"kinds":["schema"]}'::jsonb,
       model = 'claude-sonnet-4-6',
       temperature = 0.0,
       extra = CASE
           WHEN jsonb_typeof(agent.extra->'monitor'->'engines') = 'array'
           THEN jsonb_set(
               agent.extra,
               '{monitor,engines}',
               (
                   SELECT COALESCE(jsonb_agg(
                       CASE
                           WHEN engine->>'name' IN ('monte_carlo', 'bayesian_calibration')
                           THEN jsonb_set(engine, '{enabled}', 'false'::jsonb, true)
                           WHEN engine->>'name' = 'decision_orchestrator'
                           THEN jsonb_set(
                               jsonb_set(engine, '{execute_engines}', 'false'::jsonb, true),
                               '{engine_inputs}', '{}'::jsonb, true
                           )
                           ELSE engine
                       END
                       ORDER BY ordinal
                   ), '[]'::jsonb)
                     FROM jsonb_array_elements(agent.extra->'monitor'->'engines')
                          WITH ORDINALITY AS configured(engine, ordinal)
               ),
               false
           )
           ELSE agent.extra
       END,
       updated_at = clock_timestamp()
 WHERE agent.cartridge_id = 'sap_successfactors'
   AND agent.slug = 'sap_successfactors_talent_monitor';

CREATE TABLE IF NOT EXISTS agent_handoffs (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    workspace_id          UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    item_id               TEXT NOT NULL,
    signal_id             TEXT,
    evidence_pack_id      BIGINT NOT NULL REFERENCES evidence_packs(id) ON DELETE RESTRICT,
    producer_agent_id     UUID REFERENCES agents(id) ON DELETE SET NULL,
    producer_run_id       BIGINT REFERENCES agent_runs(id) ON DELETE SET NULL,
    analysis_run_id       BIGINT REFERENCES agent_runs(id) ON DELETE SET NULL,
    verifier_run_id       BIGINT REFERENCES agent_runs(id) ON DELETE SET NULL,
    lease_owner           UUID,
    lease_expires_at      TIMESTAMPTZ,
    attempt_count         INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    consumer_role         TEXT NOT NULL DEFAULT 'control_room_analyst',
    status                TEXT NOT NULL DEFAULT 'ready'
        CHECK (status IN (
            'ready', 'analyzing', 'verifying', 'verified', 'rejected',
            'insufficient_data', 'expired'
        )),
    schema_version        TEXT NOT NULL DEFAULT 'analysis-envelope-v1',
    version               INTEGER NOT NULL CHECK (version > 0),
    input_digest          CHAR(64) NOT NULL CHECK (input_digest ~ '^[0-9a-f]{64}$'),
    output_digest         CHAR(64) CHECK (output_digest ~ '^[0-9a-f]{64}$'),
    model                 TEXT,
    prompt_digest         CHAR(64) CHECK (prompt_digest ~ '^[0-9a-f]{64}$'),
    tool_transcript_digest CHAR(64) CHECK (tool_transcript_digest ~ '^[0-9a-f]{64}$'),
    ruleset_version       TEXT NOT NULL DEFAULT 'control-room-grounding-v1',
    grounding_status      TEXT NOT NULL DEFAULT 'pending'
        CHECK (grounding_status IN ('pending', 'verified', 'rejected', 'insufficient_data')),
    verification_reason   TEXT,
    assumptions           JSONB NOT NULL DEFAULT '[]'::jsonb,
    hypotheses            JSONB NOT NULL DEFAULT '[]'::jsonb,
    options               JSONB NOT NULL DEFAULT '[]'::jsonb,
    blockers              JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata              JSONB NOT NULL DEFAULT '{}'::jsonb,
    as_of                 TIMESTAMPTZ NOT NULL,
    expires_at            TIMESTAMPTZ NOT NULL,
    created_by_user_id    BIGINT REFERENCES users(id) ON DELETE SET NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    verified_at           TIMESTAMPTZ,
    CONSTRAINT agent_handoffs_scope_pack_fk
        FOREIGN KEY (tenant_id, workspace_id, evidence_pack_id)
        REFERENCES evidence_packs(tenant_id, workspace_id, id) ON DELETE RESTRICT,
    CONSTRAINT agent_handoffs_expiry_check CHECK (expires_at > as_of),
    CONSTRAINT agent_handoffs_lease_pair_check CHECK (
        (lease_owner IS NULL) = (lease_expires_at IS NULL)
    ),
    CONSTRAINT agent_handoffs_verified_check CHECK (
        status <> 'verified'
        OR (
            grounding_status = 'verified'
            AND output_digest IS NOT NULL
            AND verified_at IS NOT NULL
        )
    ),
    CONSTRAINT agent_handoffs_state_grounding_check CHECK (
        (status = 'verified' AND grounding_status = 'verified')
        OR (status = 'rejected' AND grounding_status = 'rejected')
        OR (status IN ('insufficient_data', 'expired')
            AND grounding_status = 'insufficient_data')
        OR (status IN ('ready', 'analyzing', 'verifying')
            AND grounding_status = 'pending')
    ),
    UNIQUE (tenant_id, workspace_id, item_id, version),
    UNIQUE (tenant_id, workspace_id, item_id, evidence_pack_id, input_digest),
    UNIQUE (tenant_id, workspace_id, id)
);

ALTER TABLE agent_handoffs
    ADD COLUMN IF NOT EXISTS lease_owner UUID;
ALTER TABLE agent_handoffs
    ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ;
ALTER TABLE agent_handoffs
    ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0;

ALTER TABLE agent_handoffs
    DROP CONSTRAINT IF EXISTS agent_handoffs_attempt_count_check;
ALTER TABLE agent_handoffs
    ADD CONSTRAINT agent_handoffs_attempt_count_check
        CHECK (attempt_count >= 0);
ALTER TABLE agent_handoffs
    DROP CONSTRAINT IF EXISTS agent_handoffs_lease_pair_check;
ALTER TABLE agent_handoffs
    ADD CONSTRAINT agent_handoffs_lease_pair_check CHECK (
        (lease_owner IS NULL) = (lease_expires_at IS NULL)
    );

-- Rows created by the pre-lease implementation are safe to retry because no
-- worker can prove ownership of them.  Reset only non-terminal work.
UPDATE agent_handoffs
   SET status = 'ready',
       grounding_status = 'pending',
       analysis_run_id = NULL,
       verifier_run_id = NULL,
       lease_owner = NULL,
       lease_expires_at = NULL,
       updated_at = clock_timestamp()
 WHERE status IN ('analyzing', 'verifying')
   AND (lease_owner IS NULL OR lease_expires_at IS NULL);

ALTER TABLE agent_handoffs
    DROP CONSTRAINT IF EXISTS agent_handoffs_active_lease_check;
ALTER TABLE agent_handoffs
    ADD CONSTRAINT agent_handoffs_active_lease_check CHECK (
        (status IN ('analyzing', 'verifying')
            AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR (status NOT IN ('analyzing', 'verifying')
            AND lease_owner IS NULL AND lease_expires_at IS NULL)
    );

CREATE INDEX IF NOT EXISTS agent_handoffs_item_latest_idx
    ON agent_handoffs (tenant_id, workspace_id, item_id, version DESC);
CREATE INDEX IF NOT EXISTS agent_handoffs_pack_idx
    ON agent_handoffs (tenant_id, workspace_id, evidence_pack_id);
DROP INDEX IF EXISTS agent_handoffs_pending_idx;
CREATE INDEX agent_handoffs_pending_idx
    ON agent_handoffs (status, lease_expires_at, expires_at)
    WHERE status IN ('ready', 'analyzing', 'verifying');

ALTER TABLE agent_handoffs
    DROP CONSTRAINT IF EXISTS agent_handoffs_state_grounding_check;
ALTER TABLE agent_handoffs
    ADD CONSTRAINT agent_handoffs_state_grounding_check CHECK (
        (status = 'verified' AND grounding_status = 'verified')
        OR (status = 'rejected' AND grounding_status = 'rejected')
        OR (status IN ('insufficient_data', 'expired')
            AND grounding_status = 'insufficient_data')
        OR (status IN ('ready', 'analyzing', 'verifying')
            AND grounding_status = 'pending')
    );

CREATE TABLE IF NOT EXISTS agent_claims (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    workspace_id          UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    handoff_id            UUID NOT NULL REFERENCES agent_handoffs(id) ON DELETE RESTRICT,
    claim_key             TEXT NOT NULL,
    claim_type            TEXT NOT NULL
        CHECK (claim_type IN (
            'observed', 'computed', 'hypothesis', 'option', 'assumption'
        )),
    statement             TEXT NOT NULL,
    value                 JSONB,
    unit                  TEXT,
    population            BIGINT CHECK (population IS NULL OR population >= 0),
    evidence_item_ids     BIGINT[] NOT NULL,
    evidence_paths        TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    formula               TEXT,
    ruleset_version       TEXT,
    verification_status   TEXT NOT NULL DEFAULT 'pending'
        CHECK (verification_status IN ('pending', 'verified', 'rejected', 'insufficient_data')),
    verification_reason   TEXT,
    metadata              JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    verified_at           TIMESTAMPTZ,
    CONSTRAINT agent_claims_evidence_required
        CHECK (cardinality(evidence_item_ids) > 0),
    CONSTRAINT agent_claims_scope_handoff_fk
        FOREIGN KEY (tenant_id, workspace_id, handoff_id)
        REFERENCES agent_handoffs(tenant_id, workspace_id, id) ON DELETE RESTRICT,
    CONSTRAINT agent_claims_observed_computed_value
        CHECK (claim_type IN ('hypothesis', 'assumption') OR value IS NOT NULL),
    CONSTRAINT agent_claims_computed_ruleset
        CHECK (claim_type <> 'computed' OR (formula IS NOT NULL AND ruleset_version IS NOT NULL)),
    CONSTRAINT agent_claims_verified_at_check CHECK (
        (verification_status = 'verified') = (verified_at IS NOT NULL)
    ),
    UNIQUE (handoff_id, claim_key)
);

CREATE INDEX IF NOT EXISTS agent_claims_handoff_idx
    ON agent_claims (tenant_id, workspace_id, handoff_id, claim_key);

ALTER TABLE agent_claims
    DROP CONSTRAINT IF EXISTS agent_claims_verified_at_check,
    DROP CONSTRAINT IF EXISTS agent_claims_claim_type_check,
    DROP CONSTRAINT IF EXISTS agent_claims_observed_computed_value,
    DROP CONSTRAINT IF EXISTS agent_claims_exact_evidence_refs;
ALTER TABLE agent_claims
    ADD CONSTRAINT agent_claims_claim_type_check CHECK (
        claim_type IN (
            'observed', 'computed', 'hypothesis', 'option', 'assumption'
        )
    ),
    ADD CONSTRAINT agent_claims_observed_computed_value CHECK (
        claim_type IN ('hypothesis', 'assumption') OR value IS NOT NULL
    ),
    ADD CONSTRAINT agent_claims_verified_at_check CHECK (
        (verification_status = 'verified') = (verified_at IS NOT NULL)
    ),
    -- Existing pre-release rows may use the old unpaired representation. They
    -- remain unpublished by the application verifier; every new row is
    -- required to preserve the exact item/path pairing.
    ADD CONSTRAINT agent_claims_exact_evidence_refs CHECK (
        cardinality(evidence_item_ids) = cardinality(evidence_paths)
        AND cardinality(evidence_item_ids) > 0
    ) NOT VALID;

CREATE TABLE IF NOT EXISTS agent_rule_proposals (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    workspace_id          UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    ruleset_name          TEXT NOT NULL,
    proposed_version      TEXT NOT NULL,
    parent_version        TEXT NOT NULL,
    proposal              JSONB NOT NULL,
    evidence_pack_ids     BIGINT[] NOT NULL,
    outcome_ids           BIGINT[] NOT NULL,
    proposal_digest       CHAR(64) NOT NULL CHECK (proposal_digest ~ '^[0-9a-f]{64}$'),
    status                TEXT NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed', 'approved', 'rejected', 'superseded')),
    proposed_by_run_id    BIGINT NOT NULL REFERENCES agent_runs(id) ON DELETE RESTRICT,
    reviewed_by_user_id   BIGINT REFERENCES users(id) ON DELETE RESTRICT,
    review_reason         TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    reviewed_at           TIMESTAMPTZ,
    activated_at          TIMESTAMPTZ,
    CONSTRAINT agent_rule_proposals_evidence_required CHECK (
        cardinality(evidence_pack_ids) > 0
    ),
    CONSTRAINT agent_rule_proposals_outcomes_required CHECK (
        cardinality(outcome_ids) > 0
    ),
    CONSTRAINT agent_rule_proposals_payload_check CHECK (
        jsonb_typeof(proposal) = 'object' AND proposal <> '{}'::jsonb
    ),
    CONSTRAINT agent_rule_proposals_review_state_check CHECK (
        (
            status = 'proposed'
            AND reviewed_by_user_id IS NULL
            AND review_reason IS NULL
            AND reviewed_at IS NULL
        ) OR (
            status IN ('approved', 'rejected', 'superseded')
            AND reviewed_by_user_id IS NOT NULL
            AND NULLIF(btrim(review_reason), '') IS NOT NULL
            AND reviewed_at IS NOT NULL
        )
    ),
    -- Activation is deliberately unavailable in this phase.  A later release
    -- must introduce a distinct, human-authorized activation transition.
    CONSTRAINT agent_rule_proposals_no_auto_activation CHECK (activated_at IS NULL),
    UNIQUE (tenant_id, workspace_id, ruleset_name, proposed_version),
    UNIQUE (tenant_id, workspace_id, id)
);

ALTER TABLE agent_rule_proposals
    DROP CONSTRAINT IF EXISTS agent_rule_proposals_evidence_required,
    DROP CONSTRAINT IF EXISTS agent_rule_proposals_outcomes_required,
    DROP CONSTRAINT IF EXISTS agent_rule_proposals_payload_check,
    DROP CONSTRAINT IF EXISTS agent_rule_proposals_human_approval,
    DROP CONSTRAINT IF EXISTS agent_rule_proposals_review_state_check,
    DROP CONSTRAINT IF EXISTS agent_rule_proposals_no_auto_activation;
ALTER TABLE agent_rule_proposals
    ALTER COLUMN outcome_ids DROP DEFAULT,
    ALTER COLUMN proposed_by_run_id SET NOT NULL,
    ADD CONSTRAINT agent_rule_proposals_evidence_required CHECK (
        cardinality(evidence_pack_ids) > 0
    ),
    ADD CONSTRAINT agent_rule_proposals_outcomes_required CHECK (
        cardinality(outcome_ids) > 0
    ),
    ADD CONSTRAINT agent_rule_proposals_payload_check CHECK (
        jsonb_typeof(proposal) = 'object' AND proposal <> '{}'::jsonb
    ),
    ADD CONSTRAINT agent_rule_proposals_review_state_check CHECK (
        (
            status = 'proposed'
            AND reviewed_by_user_id IS NULL
            AND review_reason IS NULL
            AND reviewed_at IS NULL
        ) OR (
            status IN ('approved', 'rejected', 'superseded')
            AND reviewed_by_user_id IS NOT NULL
            AND NULLIF(btrim(review_reason), '') IS NOT NULL
            AND reviewed_at IS NOT NULL
        )
    ),
    ADD CONSTRAINT agent_rule_proposals_no_auto_activation CHECK (
        activated_at IS NULL
    );

CREATE INDEX IF NOT EXISTS agent_rule_proposals_status_idx
    ON agent_rule_proposals (tenant_id, workspace_id, status, created_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS prediction_outcomes_scope_id_key
    ON prediction_outcomes (tenant_id, workspace_id, id);

CREATE UNIQUE INDEX IF NOT EXISTS agent_rule_proposals_scope_id_key
    ON agent_rule_proposals (tenant_id, workspace_id, id);

CREATE TABLE IF NOT EXISTS agent_rule_proposal_evidence_refs (
    proposal_id       UUID NOT NULL,
    tenant_id         UUID NOT NULL,
    workspace_id      UUID NOT NULL,
    evidence_pack_id  BIGINT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (proposal_id, evidence_pack_id),
    CONSTRAINT agent_rule_proposal_evidence_refs_proposal_fk
        FOREIGN KEY (tenant_id, workspace_id, proposal_id)
        REFERENCES agent_rule_proposals(tenant_id, workspace_id, id)
        ON DELETE RESTRICT,
    CONSTRAINT agent_rule_proposal_evidence_refs_pack_fk
        FOREIGN KEY (tenant_id, workspace_id, evidence_pack_id)
        REFERENCES evidence_packs(tenant_id, workspace_id, id)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS agent_rule_proposal_outcome_refs (
    proposal_id  UUID NOT NULL,
    tenant_id    UUID NOT NULL,
    workspace_id UUID NOT NULL,
    outcome_id   BIGINT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (proposal_id, outcome_id),
    CONSTRAINT agent_rule_proposal_outcome_refs_proposal_fk
        FOREIGN KEY (tenant_id, workspace_id, proposal_id)
        REFERENCES agent_rule_proposals(tenant_id, workspace_id, id)
        ON DELETE RESTRICT,
    CONSTRAINT agent_rule_proposal_outcome_refs_outcome_fk
        FOREIGN KEY (tenant_id, workspace_id, outcome_id)
        REFERENCES prediction_outcomes(tenant_id, workspace_id, id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS agent_rule_proposal_evidence_refs_scope_idx
    ON agent_rule_proposal_evidence_refs(
        tenant_id, workspace_id, evidence_pack_id
    );
CREATE INDEX IF NOT EXISTS agent_rule_proposal_outcome_refs_scope_idx
    ON agent_rule_proposal_outcome_refs(tenant_id, workspace_id, outcome_id);

CREATE OR REPLACE FUNCTION materialize_agent_rule_proposal_refs()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public
AS $refs$
BEGIN
    INSERT INTO public.agent_rule_proposal_evidence_refs(
        proposal_id, tenant_id, workspace_id, evidence_pack_id
    )
    SELECT NEW.id, NEW.tenant_id, NEW.workspace_id, ref.id
      FROM unnest(NEW.evidence_pack_ids) AS ref(id);

    INSERT INTO public.agent_rule_proposal_outcome_refs(
        proposal_id, tenant_id, workspace_id, outcome_id
    )
    SELECT NEW.id, NEW.tenant_id, NEW.workspace_id, ref.id
      FROM unnest(NEW.outcome_ids) AS ref(id);
    RETURN NEW;
END
$refs$;

ALTER FUNCTION materialize_agent_rule_proposal_refs() OWNER TO postgres;
REVOKE ALL ON FUNCTION materialize_agent_rule_proposal_refs() FROM PUBLIC;

DROP TRIGGER IF EXISTS materialize_agent_rule_proposal_refs_row
    ON agent_rule_proposals;
CREATE TRIGGER materialize_agent_rule_proposal_refs_row
    AFTER INSERT ON agent_rule_proposals
    FOR EACH ROW EXECUTE FUNCTION materialize_agent_rule_proposal_refs();

CREATE OR REPLACE FUNCTION protect_agent_rule_proposal()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public
AS $proposal$
BEGIN
    -- Learning is deliberately paused.  The previous draft accepted a
    -- client-selected producer run and arbitrary JSON, which cannot establish
    -- server-owned provenance.  Keep the schema read-only for audit/migration
    -- compatibility until a dedicated learner creates typed drafts itself.
    RAISE EXCEPTION 'agent rule proposals are paused by server policy'
        USING ERRCODE = '55000';
END
$proposal$;

ALTER FUNCTION protect_agent_rule_proposal() OWNER TO postgres;
REVOKE ALL ON FUNCTION protect_agent_rule_proposal() FROM PUBLIC;

DROP TRIGGER IF EXISTS protect_agent_rule_proposal_row ON agent_rule_proposals;
CREATE TRIGGER protect_agent_rule_proposal_row
    BEFORE INSERT OR UPDATE OR DELETE ON agent_rule_proposals
    FOR EACH ROW EXECUTE FUNCTION protect_agent_rule_proposal();

CREATE OR REPLACE FUNCTION protect_sealed_evidence_pack()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public
AS $sealed$
DECLARE
    old_parent_sealed_at TIMESTAMPTZ;
    new_parent_sealed_at TIMESTAMPTZ;
BEGIN
    IF TG_TABLE_NAME = 'evidence_packs' THEN
        IF TG_OP <> 'INSERT' AND OLD.sealed_at IS NOT NULL THEN
            RAISE EXCEPTION 'sealed evidence pack is immutable'
                USING ERRCODE = '23514';
        END IF;
    ELSE
        -- OLD and NEW are separate authority boundaries.  COALESCE is unsafe
        -- here: during UPDATE it would inspect only NEW and permit an item to
        -- escape from a sealed pack by being reparented to an unsealed one.
        IF TG_OP IN ('UPDATE', 'DELETE') THEN
            SELECT pack.sealed_at
              INTO old_parent_sealed_at
              FROM public.evidence_packs pack
             WHERE pack.id = OLD.evidence_pack_id
             FOR UPDATE;
            IF old_parent_sealed_at IS NOT NULL THEN
                RAISE EXCEPTION 'items in a sealed evidence pack are immutable'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
        IF TG_OP IN ('INSERT', 'UPDATE') THEN
            SELECT pack.sealed_at
              INTO new_parent_sealed_at
              FROM public.evidence_packs pack
             WHERE pack.id = NEW.evidence_pack_id
             FOR UPDATE;
            IF new_parent_sealed_at IS NOT NULL THEN
                RAISE EXCEPTION 'items in a sealed evidence pack are immutable'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END
$sealed$;

ALTER FUNCTION protect_sealed_evidence_pack() OWNER TO postgres;
REVOKE ALL ON FUNCTION protect_sealed_evidence_pack() FROM PUBLIC;

DROP TRIGGER IF EXISTS protect_sealed_evidence_pack_row ON evidence_packs;
CREATE TRIGGER protect_sealed_evidence_pack_row
    BEFORE UPDATE OR DELETE ON evidence_packs
    FOR EACH ROW EXECUTE FUNCTION protect_sealed_evidence_pack();

DROP TRIGGER IF EXISTS protect_sealed_evidence_items ON evidence_items;
CREATE TRIGGER protect_sealed_evidence_items
    BEFORE INSERT OR UPDATE OR DELETE ON evidence_items
    FOR EACH ROW EXECUTE FUNCTION protect_sealed_evidence_pack();

CREATE OR REPLACE FUNCTION protect_verified_agent_analysis()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public
AS $protect$
DECLARE
    old_parent_status TEXT;
    old_parent_tenant UUID;
    old_parent_workspace UUID;
    old_parent_pack BIGINT;
    new_parent_status TEXT;
    new_parent_tenant UUID;
    new_parent_workspace UUID;
    new_parent_pack BIGINT;
    parent_sealed_at TIMESTAMPTZ;
    claim_count BIGINT;
    unverified_claim_count BIGINT;
    invalid_claim_evidence_count BIGINT;
    analysis_run_status TEXT;
    verifier_run_status TEXT;
    prior_producer_run_status TEXT;
    analysis_run_finished_at TIMESTAMPTZ;
    verifier_run_finished_at TIMESTAMPTZ;
    prior_producer_run_finished_at TIMESTAMPTZ;
    analysis_run_tenant UUID;
    analysis_run_workspace UUID;
    analysis_run_agent UUID;
    verifier_run_tenant UUID;
    verifier_run_workspace UUID;
BEGIN
    IF TG_TABLE_NAME = 'agent_handoffs' THEN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'agent handoffs are an append-only ledger'
                USING ERRCODE = '23514';
        END IF;
        IF TG_OP = 'INSERT' THEN
            -- A handoff is born without any producer/verifier authority.  Runs,
            -- claims and attestations are attached only through subsequent
            -- server-owned lifecycle transitions.  This prevents a direct
            -- INSERT from materializing a forged terminal result.
            IF NEW.status <> 'ready'
               OR NEW.grounding_status <> 'pending'
               OR NEW.producer_agent_id IS NOT NULL
               OR NEW.producer_run_id IS NOT NULL
               OR NEW.analysis_run_id IS NOT NULL
               OR NEW.verifier_run_id IS NOT NULL
               OR NEW.lease_owner IS NOT NULL
               OR NEW.lease_expires_at IS NOT NULL
               OR NEW.attempt_count <> 0
               OR NEW.output_digest IS NOT NULL
               OR NEW.verification_reason IS NOT NULL
               OR NEW.verified_at IS NOT NULL
               OR NEW.hypotheses <> '[]'::jsonb
               OR NEW.options <> '[]'::jsonb
               OR NEW.assumptions <> '[]'::jsonb THEN
                RAISE EXCEPTION 'agent handoff must start ready and unverified'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
        IF TG_OP = 'UPDATE' AND (
            NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
            OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id
            OR NEW.item_id IS DISTINCT FROM OLD.item_id
            OR NEW.signal_id IS DISTINCT FROM OLD.signal_id
            OR NEW.evidence_pack_id IS DISTINCT FROM OLD.evidence_pack_id
            OR NEW.consumer_role IS DISTINCT FROM OLD.consumer_role
            OR NEW.version IS DISTINCT FROM OLD.version
            OR NEW.input_digest IS DISTINCT FROM OLD.input_digest
            OR NEW.as_of IS DISTINCT FROM OLD.as_of
            OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
        ) THEN
            RAISE EXCEPTION 'agent handoff evidence identity is immutable'
                USING ERRCODE = '23514';
        END IF;
        IF TG_OP = 'UPDATE' AND (
            NEW.producer_agent_id IS DISTINCT FROM OLD.producer_agent_id
            OR NEW.producer_run_id IS DISTINCT FROM OLD.producer_run_id
        ) THEN
            IF OLD.status <> 'ready'
               OR NEW.status <> 'analyzing'
               OR NEW.producer_agent_id IS NULL
               OR NEW.producer_run_id IS NULL
               OR NEW.producer_run_id IS DISTINCT FROM NEW.analysis_run_id
               OR NOT EXISTS (
                   SELECT 1
                     FROM public.agent_runs run
                    WHERE run.id = NEW.producer_run_id
                      AND run.agent_id = NEW.producer_agent_id
                      AND run.tenant_id = NEW.tenant_id
                      AND run.workspace_id = NEW.workspace_id
                      AND run.status = 'running'
               ) THEN
                RAISE EXCEPTION 'agent handoff producer provenance is invalid'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.producer_run_id IS NOT NULL THEN
                SELECT run.status, run.finished_at
                  INTO prior_producer_run_status,
                       prior_producer_run_finished_at
                  FROM public.agent_runs run
                 WHERE run.id = OLD.producer_run_id;
                IF prior_producer_run_status NOT IN ('ok', 'error', 'cancelled')
                   OR prior_producer_run_finished_at IS NULL THEN
                    RAISE EXCEPTION 'agent handoff producer retry is invalid'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
        END IF;
        IF TG_OP <> 'INSERT' AND OLD.status = 'verified' THEN
            RAISE EXCEPTION 'verified agent handoff is immutable'
                USING ERRCODE = '23514';
        END IF;
        IF TG_OP <> 'DELETE' AND NEW.status = 'verified' THEN
            SELECT run.status, run.finished_at, run.tenant_id,
                   run.workspace_id, run.agent_id
              INTO analysis_run_status, analysis_run_finished_at,
                   analysis_run_tenant, analysis_run_workspace,
                   analysis_run_agent
              FROM public.agent_runs run
             WHERE run.id = NEW.analysis_run_id;
            SELECT run.status, run.finished_at, run.tenant_id, run.workspace_id
              INTO verifier_run_status, verifier_run_finished_at,
                   verifier_run_tenant, verifier_run_workspace
              FROM public.agent_runs run
             WHERE run.id = NEW.verifier_run_id;
            IF analysis_run_status IS DISTINCT FROM 'ok'
               OR verifier_run_status IS DISTINCT FROM 'ok'
               OR analysis_run_finished_at IS NULL
               OR verifier_run_finished_at IS NULL
               OR analysis_run_tenant IS DISTINCT FROM NEW.tenant_id
               OR verifier_run_tenant IS DISTINCT FROM NEW.tenant_id
               OR analysis_run_workspace IS DISTINCT FROM NEW.workspace_id
               OR verifier_run_workspace IS DISTINCT FROM NEW.workspace_id
               OR NEW.producer_run_id IS DISTINCT FROM NEW.analysis_run_id
               OR NEW.producer_agent_id IS DISTINCT FROM analysis_run_agent THEN
                RAISE EXCEPTION 'verified handoff requires completed scoped agent runs'
                    USING ERRCODE = '23514';
            END IF;
            SELECT pack.sealed_at
              INTO parent_sealed_at
              FROM public.evidence_packs pack
             WHERE pack.id = NEW.evidence_pack_id
               AND pack.tenant_id = NEW.tenant_id
               AND pack.workspace_id = NEW.workspace_id;
            IF parent_sealed_at IS NULL THEN
                RAISE EXCEPTION 'verified handoff requires sealed evidence'
                    USING ERRCODE = '23514';
            END IF;
            SELECT count(*), count(*) FILTER (
                       WHERE claim.verification_status <> 'verified'
                           OR claim.verified_at IS NULL
                   )
              INTO claim_count, unverified_claim_count
              FROM public.agent_claims claim
             WHERE claim.handoff_id = NEW.id;
            IF claim_count = 0 OR unverified_claim_count <> 0 THEN
                RAISE EXCEPTION 'verified handoff requires verified claims'
                    USING ERRCODE = '23514';
            END IF;
            SELECT count(DISTINCT claim.id)
              INTO invalid_claim_evidence_count
              FROM public.agent_claims claim
             WHERE claim.handoff_id = NEW.id
               AND (
                   claim.tenant_id IS DISTINCT FROM NEW.tenant_id
                   OR claim.workspace_id IS DISTINCT FROM NEW.workspace_id
                   OR cardinality(claim.evidence_item_ids)
                        <> cardinality(claim.evidence_paths)
                   OR EXISTS (
                       SELECT 1
                         FROM unnest(
                                  claim.evidence_item_ids,
                                  claim.evidence_paths
                              ) AS ref(evidence_id, evidence_path)
                        WHERE NOT EXISTS (
                            SELECT 1
                              FROM public.evidence_items evidence
                             WHERE evidence.id = ref.evidence_id
                               AND evidence.tenant_id = NEW.tenant_id
                               AND evidence.workspace_id = NEW.workspace_id
                               AND evidence.evidence_pack_id = NEW.evidence_pack_id
                               AND (
                                   (ref.evidence_path LIKE 'data.%'
                                    AND evidence.data
                                        ? split_part(ref.evidence_path, '.', 2))
                                   OR
                                   (ref.evidence_path LIKE 'metadata.%'
                                    AND evidence.metadata
                                        ? split_part(ref.evidence_path, '.', 2))
                               )
                        )
                        OR ref.evidence_path
                            !~ '^(data|metadata)\.[a-z0-9_]{1,80}$'
                   )
                   OR EXISTS (
                       SELECT 1
                         FROM unnest(
                                  claim.evidence_item_ids,
                                  claim.evidence_paths
                              ) AS duplicate_ref(evidence_id, evidence_path)
                        GROUP BY duplicate_ref.evidence_id,
                                 duplicate_ref.evidence_path
                       HAVING count(*) > 1
                   )
               );
            IF invalid_claim_evidence_count <> 0 THEN
                RAISE EXCEPTION 'verified claim evidence is outside the bound pack'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.hypotheses <> '[]'::jsonb
               OR NEW.options <> '[]'::jsonb
               OR NEW.assumptions <> '[]'::jsonb THEN
                RAISE EXCEPTION 'verified output must be reconstructed from claims'
                    USING ERRCODE = '23514';
            END IF;
            IF jsonb_typeof(NEW.metadata->'verifier_attestation')
                    IS DISTINCT FROM 'object'
               OR NULLIF(
                      NEW.metadata->'verifier_attestation'->>'key_id', ''
                  ) IS NULL
               OR NEW.metadata->'verifier_attestation'->>'digest'
                    IS DISTINCT FROM NEW.output_digest
               OR COALESCE(
                      NEW.metadata->'verifier_attestation'->>'signature', ''
                  ) !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'verified handoff requires verifier attestation'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
    ELSE
        -- Check the old parent independently before consulting NEW.  Otherwise
        -- an UPDATE can move a claim out of a verified handoff and make the
        -- immutable row appear to belong to an editable parent.
        IF TG_OP IN ('UPDATE', 'DELETE') THEN
            SELECT handoff.status, handoff.tenant_id, handoff.workspace_id,
                   handoff.evidence_pack_id
              INTO old_parent_status, old_parent_tenant,
                   old_parent_workspace, old_parent_pack
              FROM public.agent_handoffs handoff
             WHERE handoff.id = OLD.handoff_id
             FOR UPDATE;
            IF old_parent_status IS NULL THEN
                RAISE EXCEPTION 'claim old parent handoff is unavailable'
                    USING ERRCODE = '23503';
            END IF;
            IF old_parent_status = 'verified' THEN
                RAISE EXCEPTION 'claims for a verified handoff are immutable'
                    USING ERRCODE = '23514';
            END IF;
        END IF;

        IF TG_OP IN ('INSERT', 'UPDATE') THEN
            SELECT handoff.status, handoff.tenant_id, handoff.workspace_id,
                   handoff.evidence_pack_id
              INTO new_parent_status, new_parent_tenant,
                   new_parent_workspace, new_parent_pack
              FROM public.agent_handoffs handoff
             WHERE handoff.id = NEW.handoff_id
             FOR UPDATE;
            IF new_parent_status IS NULL THEN
                RAISE EXCEPTION 'claim new parent handoff is unavailable'
                    USING ERRCODE = '23503';
            END IF;
            IF new_parent_status = 'verified' THEN
                RAISE EXCEPTION 'claims for a verified handoff are immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF TG_OP = 'UPDATE'
               AND NEW.handoff_id IS DISTINCT FROM OLD.handoff_id THEN
                RAISE EXCEPTION 'claim parent handoff is immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.tenant_id IS DISTINCT FROM new_parent_tenant
               OR NEW.workspace_id IS DISTINCT FROM new_parent_workspace THEN
                RAISE EXCEPTION 'claim scope does not match parent handoff'
                    USING ERRCODE = '23514';
            END IF;
            IF cardinality(NEW.evidence_item_ids)
                    <> cardinality(NEW.evidence_paths)
               OR EXISTS (
                   SELECT 1
                     FROM unnest(
                              NEW.evidence_item_ids,
                              NEW.evidence_paths
                          ) AS ref(evidence_id, evidence_path)
                    WHERE ref.evidence_path
                            !~ '^(data|metadata)\.[a-z0-9_]{1,80}$'
                       OR NOT EXISTS (
                           SELECT 1
                             FROM public.evidence_items evidence
                            WHERE evidence.id = ref.evidence_id
                              AND evidence.tenant_id = new_parent_tenant
                              AND evidence.workspace_id = new_parent_workspace
                              AND evidence.evidence_pack_id = new_parent_pack
                              AND (
                                  (ref.evidence_path LIKE 'data.%'
                                   AND evidence.data
                                       ? split_part(ref.evidence_path, '.', 2))
                                  OR
                                  (ref.evidence_path LIKE 'metadata.%'
                                   AND evidence.metadata
                                       ? split_part(ref.evidence_path, '.', 2))
                              )
                       )
               )
               OR EXISTS (
                   SELECT 1
                     FROM unnest(
                              NEW.evidence_item_ids,
                              NEW.evidence_paths
                          ) AS duplicate_ref(evidence_id, evidence_path)
                    GROUP BY duplicate_ref.evidence_id,
                             duplicate_ref.evidence_path
                   HAVING count(*) > 1
               ) THEN
                RAISE EXCEPTION 'claim evidence is outside the bound evidence pack or exact path'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END
$protect$;

ALTER FUNCTION protect_verified_agent_analysis() OWNER TO postgres;
REVOKE ALL ON FUNCTION protect_verified_agent_analysis() FROM PUBLIC;

DROP TRIGGER IF EXISTS protect_verified_agent_handoff ON agent_handoffs;
CREATE TRIGGER protect_verified_agent_handoff
    BEFORE INSERT OR UPDATE OR DELETE ON agent_handoffs
    FOR EACH ROW EXECUTE FUNCTION protect_verified_agent_analysis();

DROP TRIGGER IF EXISTS protect_verified_agent_claims ON agent_claims;
CREATE TRIGGER protect_verified_agent_claims
    BEFORE INSERT OR UPDATE OR DELETE ON agent_claims
    FOR EACH ROW EXECUTE FUNCTION protect_verified_agent_analysis();

ALTER TABLE agent_handoffs ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_handoffs FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_claims ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_claims FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_rule_proposals ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_rule_proposals FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_rule_proposal_evidence_refs ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_rule_proposal_evidence_refs FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_rule_proposal_outcome_refs ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_rule_proposal_outcome_refs FORCE ROW LEVEL SECURITY;

DO $policies$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_console') THEN
        RAISE EXCEPTION 'omega_console role is required for grounded analysis';
    END IF;

    DROP POLICY IF EXISTS agent_handoffs_console_scope ON agent_handoffs;
    CREATE POLICY agent_handoffs_console_scope ON agent_handoffs
        FOR ALL TO omega_console
        USING (omega_rls_workspace_matches(tenant_id, workspace_id))
        WITH CHECK (omega_rls_workspace_matches(tenant_id, workspace_id));

    DROP POLICY IF EXISTS agent_claims_console_scope ON agent_claims;
    CREATE POLICY agent_claims_console_scope ON agent_claims
        FOR ALL TO omega_console
        USING (omega_rls_workspace_matches(tenant_id, workspace_id))
        WITH CHECK (omega_rls_workspace_matches(tenant_id, workspace_id));

    DROP POLICY IF EXISTS agent_rule_proposals_console_scope ON agent_rule_proposals;
    CREATE POLICY agent_rule_proposals_console_scope ON agent_rule_proposals
        FOR SELECT TO omega_console
        USING (omega_rls_workspace_matches(tenant_id, workspace_id));

    DROP POLICY IF EXISTS agent_rule_proposal_evidence_refs_console_scope
        ON agent_rule_proposal_evidence_refs;
    CREATE POLICY agent_rule_proposal_evidence_refs_console_scope
        ON agent_rule_proposal_evidence_refs
        FOR SELECT TO omega_console
        USING (omega_rls_workspace_matches(tenant_id, workspace_id));

    DROP POLICY IF EXISTS agent_rule_proposal_outcome_refs_console_scope
        ON agent_rule_proposal_outcome_refs;
    CREATE POLICY agent_rule_proposal_outcome_refs_console_scope
        ON agent_rule_proposal_outcome_refs
        FOR SELECT TO omega_console
        USING (omega_rls_workspace_matches(tenant_id, workspace_id));
END
$policies$;

REVOKE ALL ON agent_handoffs, agent_claims, agent_rule_proposals,
    agent_rule_proposal_evidence_refs, agent_rule_proposal_outcome_refs
    FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE, DELETE
    ON agent_handoffs, agent_claims TO omega_console;
GRANT SELECT ON agent_rule_proposals TO omega_console;
GRANT SELECT ON agent_rule_proposal_evidence_refs,
    agent_rule_proposal_outcome_refs TO omega_console;

-- Fresh Docker volumes execute this file directly, outside the day-two
-- migration runner, so keep the canonical self-registration contract.  When
-- the runner applies it to an existing volume, its checksum-aware UPSERT
-- fills this row's checksum in the same transaction.
INSERT INTO schema_migrations(filename, applied_at)
VALUES ('99zzzzg_control_room_grounded_analysis.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
