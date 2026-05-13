-- Sprint v1.3 — datasets.workspace_id (RLS hardening, CRIT-4).
--
-- Before this migration, `datasets` rows had no tenancy column. The
-- workspace API trusted the dataset name alone, so a user from
-- workspace A could read workspace B's dataset just by guessing or
-- enumerating names. This migration adds workspace_id (NOT NULL after
-- backfill), wires it to workspaces(id), and indexes it for the lookup
-- the workspace API now does.

-- 1. Add the column nullable (so the ADD doesn't fail on existing rows).
ALTER TABLE datasets
    ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces(id);

-- 2. Backfill: every existing dataset belongs to the very first workspace
--    (the one created by /local_dev_bootstrap or the initial install).
--    If the workspaces table is empty for some reason, the NOT NULL
--    constraint below will fail loudly — that's intentional, we cannot
--    silently leave datasets unassigned.
UPDATE datasets
   SET workspace_id = (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)
 WHERE workspace_id IS NULL;

-- 3. Make NOT NULL now that every row has a workspace.
ALTER TABLE datasets
    ALTER COLUMN workspace_id SET NOT NULL;

-- 4. Index so the API filter (WHERE name = $1 AND workspace_id = $2) stays
--    cheap even when the dataset count grows.
CREATE INDEX IF NOT EXISTS idx_datasets_workspace_id
    ON datasets(workspace_id);
