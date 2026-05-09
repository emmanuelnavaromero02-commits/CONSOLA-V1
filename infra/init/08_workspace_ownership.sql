-- MODecissionsPaaS — workspace ownership + visibility for datasets and apps.
-- Workspace assistant prefixes user-created objects with `wk_<uid>_` and
-- stamps `created_by_id`. Apps additionally get a `visibility` field so the
-- workspace gallery shows only owned + shared apps.

CREATE TABLE IF NOT EXISTS analytic_apps (
    name         TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    html         TEXT NOT NULL,
    description  TEXT,
    cartridge_id TEXT,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE datasets       ADD COLUMN IF NOT EXISTS created_by_id BIGINT REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE analytic_apps  ADD COLUMN IF NOT EXISTS created_by_id BIGINT REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE analytic_apps  ADD COLUMN IF NOT EXISTS visibility    TEXT NOT NULL DEFAULT 'private';

CREATE INDEX IF NOT EXISTS idx_datasets_owner       ON datasets(created_by_id);
CREATE INDEX IF NOT EXISTS idx_analytic_apps_owner  ON analytic_apps(created_by_id);
CREATE INDEX IF NOT EXISTS idx_analytic_apps_vis    ON analytic_apps(visibility);

-- Apps that already exist (created before workspace had ownership) become
-- 'shared' so they remain visible to everyone in the gallery.
UPDATE analytic_apps SET visibility = 'shared' WHERE created_by_id IS NULL;
