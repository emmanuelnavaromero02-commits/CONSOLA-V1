-- Prompt 9A/9B: company onboarding metadata and operational RLS guardrails.
--
-- This migration intentionally keeps auth/provisioning tables on the
-- platform-owner allowlist. Login resolves users/workspaces before a request
-- scoped app.tenant_id/app.workspace_id exists, and company onboarding must be
-- able to create tenants/workspaces atomically. The policy guard below makes
-- that exception explicit while preventing new scoped operational tables from
-- silently relying on unscoped platform-owner policies.

ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS slug TEXT,
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

WITH normalized AS (
    SELECT
        id,
        COALESCE(
            NULLIF(
                regexp_replace(
                    regexp_replace(lower(name), '[^a-z0-9]+', '-', 'g'),
                    '(^-+|-+$)',
                    '',
                    'g'
                ),
                ''
            ),
            'tenant'
        ) AS base_slug
    FROM tenants
    WHERE slug IS NULL OR btrim(slug) = ''
),
ranked AS (
    SELECT
        id,
        base_slug,
        row_number() OVER (PARTITION BY base_slug ORDER BY id) AS rn
    FROM normalized
)
UPDATE tenants t
   SET slug = CASE
        WHEN ranked.rn = 1 THEN left(ranked.base_slug, 64)
        ELSE left(ranked.base_slug, 55) || '-' || left(t.id::text, 8)
       END
  FROM ranked
 WHERE t.id = ranked.id;

ALTER TABLE tenants
    ALTER COLUMN slug SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_tenants_slug
    ON tenants(slug);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'tenants_status_check'
           AND conrelid = 'tenants'::regclass
    ) THEN
        ALTER TABLE tenants
            ADD CONSTRAINT tenants_status_check
            CHECK (status IN ('active', 'suspended', 'archived'));
    END IF;
END $$;

CREATE OR REPLACE FUNCTION omega_touch_tenants_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS tenants_updated_at_trg ON tenants;
CREATE TRIGGER tenants_updated_at_trg
BEFORE UPDATE ON tenants
FOR EACH ROW
EXECUTE FUNCTION omega_touch_tenants_updated_at();

-- Documented allowlist for platform-owner USING(true) policies. Any scoped
-- operational table outside this list should fail the contract test if it
-- grants omega_console/omega_refinement unscoped visibility.
CREATE TABLE IF NOT EXISTS omega_rls_platform_owner_allowlist (
    table_name TEXT PRIMARY KEY,
    reason     TEXT NOT NULL,
    expires_on DATE
);

INSERT INTO omega_rls_platform_owner_allowlist (table_name, reason, expires_on)
VALUES
    ('tenants', 'platform provisioning/login bootstrap requires global tenant lookup', NULL),
    ('workspaces', 'platform provisioning/login bootstrap requires global workspace lookup', NULL),
    ('users', 'auth bootstrap resolves user before request scoped GUCs exist', NULL),
    ('user_workspace_roles', 'auth bootstrap resolves workspace memberships before request scoped GUCs exist', NULL),
    ('audit_events', 'platform audit review is intentionally global for platform admins', NULL),
    ('login_attempts', 'security lockout/audit is intentionally global', NULL)
ON CONFLICT (table_name) DO UPDATE
SET reason = EXCLUDED.reason,
    expires_on = EXCLUDED.expires_on;

DO $$
DECLARE
    tbl text;
BEGIN
    -- Apply explicit FORCE RLS to the critical scoped operational tables that
    -- already have native scoped policies in 99d/99e/99f. This PR does not
    -- drop auth/provisioning platform-owner policies; the test guard prevents
    -- accidental expansion of that exception.
    FOREACH tbl IN ARRAY ARRAY[
        'datasets',
        'decisions',
        'decision_actions',
        'control_room_items',
        'control_room_item_events',
        'control_room_action_executions',
        'control_room_thresholds',
        'control_room_lessons',
        'pipeline_runs',
        'copilot_goals',
        'copilot_lessons',
        'metric_baselines',
        'intelligence_signals',
        'evidence_packs',
        'evidence_items',
        'hypotheses',
        'decision_options',
        'prediction_outcomes',
        'external_intelligence_sources',
        'external_evidence_cache'
    ]
    LOOP
        IF to_regclass('public.' || tbl) IS NOT NULL THEN
            EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', tbl);
            EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', tbl);
        END IF;
    END LOOP;
END $$;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99m_company_onboarding_rls.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
