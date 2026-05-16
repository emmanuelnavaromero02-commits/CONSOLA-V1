-- Sprint v1.43.3 — Regression fix: SAP cartridges need CREATE on
-- schema public AND ownership of the ``jobs`` table for their
-- ``job_runner`` to start cleanly. Symptoms before this migration:
--
--   job_runner: InsufficientPrivilegeError: permission denied
--               for schema public
--   job_runner: InsufficientPrivilegeError: must be owner of
--               table jobs
--
-- v1.40.3 (migration 37) already gave ``omega_cartridge_replicon``
-- CREATE on schema public AND ownership of ``jobs`` + friends.
-- That implicitly broke the three SAP cartridge roles: every SAP
-- cartridge's ``job_runner`` issues an ``ALTER TABLE jobs ADD
-- COLUMN IF NOT EXISTS …`` on boot (idempotent self-heal for
-- older volumes), and ``ALTER TABLE`` requires ownership.
-- ``omega_cartridge_sap_*`` had INSERT/UPDATE/SELECT on jobs
-- (migration 36) but no ownership, so the cartridge crashed
-- immediately on fresh volumes.
--
-- This migration:
--
--   1. GRANTs CREATE on schema public to the 3 SAP roles (replicon
--      already has it; the GRANT is idempotent).
--   2. Creates a dedicated NOLOGIN role ``omega_cartridge_jobs_owner``
--      whose sole purpose is to own ``jobs`` + ``idx_jobs_status``.
--   3. Transfers ownership of ``jobs`` and ``idx_jobs_status`` to
--      ``omega_cartridge_jobs_owner``.
--   4. GRANTs membership of ``omega_cartridge_jobs_owner`` to all 4
--      cartridge roles so any of them can ``ALTER TABLE`` jobs.
--   5. Self-registers in schema_migrations.
--
-- The "shared owner via NOLOGIN role" pattern keeps the cartridge
-- login roles least-privileged: they inherit table ownership only
-- when they SET ROLE / get implicit-membership semantics, and the
-- owner role itself cannot be used to log in (NOLOGIN), so its
-- compromise surface is zero.
--
-- Idempotent end-to-end: every step is wrapped in EXISTS / IF NOT
-- EXISTS so re-running this migration on a DB that was already
-- patched manually (e.g. via the v1.43.2 hotfix workaround) is a
-- no-op.

-- ─────────────────────────────────────────────────────────────
-- Step 1: GRANT CREATE on schema public for the cartridge roles.
-- ─────────────────────────────────────────────────────────────
DO $$
DECLARE
  role_name TEXT;
BEGIN
  FOREACH role_name IN ARRAY ARRAY[
    'omega_cartridge_sap_hcm',
    'omega_cartridge_sap_s4',
    'omega_cartridge_sap_sf',
    'omega_cartridge_replicon'
  ]
  LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
      EXECUTE format('GRANT CREATE ON SCHEMA public TO %I', role_name);
      RAISE NOTICE 'Granted CREATE on public to %', role_name;
    ELSE
      RAISE NOTICE 'Role % does not exist, skipping', role_name;
    END IF;
  END LOOP;
END $$;

-- ─────────────────────────────────────────────────────────────
-- Step 2: Create the shared NOLOGIN owner role.
--
-- NOLOGIN is deliberate: the role exists solely to hold ownership
-- of ``jobs`` and to be granted to the cartridge login roles. It
-- has no password and cannot be used to connect, so even if its
-- name leaks, there's no authentication surface to attack.
-- ─────────────────────────────────────────────────────────────
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname = 'omega_cartridge_jobs_owner'
  ) THEN
    CREATE ROLE omega_cartridge_jobs_owner NOLOGIN;
    RAISE NOTICE 'Created role omega_cartridge_jobs_owner';
  ELSE
    RAISE NOTICE 'Role omega_cartridge_jobs_owner already exists';
  END IF;
END $$;

-- ─────────────────────────────────────────────────────────────
-- Step 3: Transfer ownership of ``jobs`` and its index.
--
-- Wrapped in existence checks so this is safe on a partial install
-- (rare, but the v1.43.2 hotfix sprint exposed enough environment
-- drift that we don't assume tables exist).
-- ─────────────────────────────────────────────────────────────
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'jobs'
  ) THEN
    -- Skip the ALTER if ownership is already what we want, so the
    -- migration is a no-op on already-patched DBs (and the NOTICE
    -- log reflects reality).
    IF (
      SELECT pg_catalog.pg_get_userbyid(c.relowner)
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
      WHERE n.nspname = 'public' AND c.relname = 'jobs'
    ) <> 'omega_cartridge_jobs_owner' THEN
      ALTER TABLE public.jobs OWNER TO omega_cartridge_jobs_owner;
      RAISE NOTICE 'Transferred jobs table ownership to omega_cartridge_jobs_owner';
    ELSE
      RAISE NOTICE 'jobs already owned by omega_cartridge_jobs_owner';
    END IF;
  ELSE
    RAISE NOTICE 'Table public.jobs not found, skipping ownership transfer';
  END IF;

  IF EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = 'public' AND indexname = 'idx_jobs_status'
  ) THEN
    -- ALTER INDEX OWNER is a no-op if already correct, so no extra
    -- guard needed here.
    ALTER INDEX public.idx_jobs_status OWNER TO omega_cartridge_jobs_owner;
    RAISE NOTICE 'Set idx_jobs_status ownership to omega_cartridge_jobs_owner';
  ELSE
    RAISE NOTICE 'Index idx_jobs_status not found, skipping';
  END IF;
END $$;

-- ─────────────────────────────────────────────────────────────
-- Step 4: GRANT membership of jobs_owner to every cartridge role.
--
-- Membership lets each role SET ROLE to jobs_owner (and Postgres
-- treats them as implicit owners for ownership-required ops like
-- ALTER TABLE). The GRANT is idempotent in modern Postgres but we
-- wrap it in the role-existence check anyway so a missing role
-- doesn't abort the whole DO block.
-- ─────────────────────────────────────────────────────────────
DO $$
DECLARE
  role_name TEXT;
BEGIN
  FOREACH role_name IN ARRAY ARRAY[
    'omega_cartridge_sap_hcm',
    'omega_cartridge_sap_s4',
    'omega_cartridge_sap_sf',
    'omega_cartridge_replicon'
  ]
  LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
      EXECUTE format(
        'GRANT omega_cartridge_jobs_owner TO %I', role_name
      );
      RAISE NOTICE 'Granted jobs_owner membership to %', role_name;
    END IF;
  END LOOP;
END $$;

-- ─────────────────────────────────────────────────────────────
-- Step 5: Register this migration in schema_migrations.
--
-- docker-entrypoint-initdb.d runs init scripts on a fresh data
-- directory but never invokes scripts/apply_db_migrations.sh.
-- Self-registering keeps the audit trail honest on both paths.
-- (Same pattern as migrations 43, 44, 45.)
-- ─────────────────────────────────────────────────────────────
INSERT INTO schema_migrations (filename, applied_at)
VALUES ('46_sap_jobs_permissions.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
