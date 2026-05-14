-- Sprint v1.32: decisions are workspace-scoped.
--
-- Without this column, workspace /api/decisions can leak shared/admin-visible
-- decisions across workspaces. Existing rows are backfilled to the oldest
-- workspace so legacy local/demo data remains reachable.

ALTER TABLE decisions
    ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces(id);

UPDATE decisions
   SET workspace_id = (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)
 WHERE workspace_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_decisions_workspace_id
    ON decisions(workspace_id);

CREATE INDEX IF NOT EXISTS idx_decisions_workspace_status
    ON decisions(workspace_id, status, commitment_date);
