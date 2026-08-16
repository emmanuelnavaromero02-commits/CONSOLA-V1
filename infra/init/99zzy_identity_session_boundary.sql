-- F-SEG Wave 1: fail-closed identity/session boundary.
--
-- omega_auth is deliberately NOLOGIN.  Application roles can only reach auth
-- state through the narrow SECURITY DEFINER functions granted at the end of
-- this migration; a compromised service cannot open a connection as omega_auth.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_auth') THEN
        CREATE ROLE omega_auth NOLOGIN NOBYPASSRLS;
    END IF;
END $$;

ALTER TABLE public.user_sessions
    ADD COLUMN IF NOT EXISTS token_hash varchar(64);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'user_sessions'
           AND column_name = 'token'
    ) THEN
        EXECUTE $sql$
            UPDATE public.user_sessions
               SET token_hash = encode(digest(token, 'sha256'), 'hex')
             WHERE token_hash IS NULL
        $sql$;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.user_sessions WHERE token_hash IS NULL) THEN
        RAISE EXCEPTION 'cannot harden user_sessions: legacy token hash backfill incomplete';
    END IF;
END $$;

ALTER TABLE public.user_sessions
    ALTER COLUMN token_hash SET NOT NULL;
ALTER TABLE public.user_sessions
    DROP CONSTRAINT IF EXISTS user_sessions_pkey;
ALTER TABLE public.user_sessions
    DROP COLUMN IF EXISTS token;
ALTER TABLE public.user_sessions
    ADD CONSTRAINT user_sessions_pkey PRIMARY KEY (token_hash);
ALTER TABLE public.user_sessions
    ADD CONSTRAINT user_sessions_token_hash_shape
    CHECK (token_hash ~ '^[0-9a-f]{64}$') NOT VALID;
ALTER TABLE public.user_sessions
    VALIDATE CONSTRAINT user_sessions_token_hash_shape;

ALTER TABLE public.user_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_sessions FORCE ROW LEVEL SECURITY;
ALTER TABLE public.refresh_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.refresh_tokens FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS user_sessions_auth_boundary_rls ON public.user_sessions;
CREATE POLICY user_sessions_auth_boundary_rls ON public.user_sessions
    FOR ALL TO omega_auth
    USING (true)
    WITH CHECK (true);

DROP POLICY IF EXISTS refresh_tokens_auth_boundary_rls ON public.refresh_tokens;
CREATE POLICY refresh_tokens_auth_boundary_rls ON public.refresh_tokens
    FOR ALL TO omega_auth
    USING (true)
    WITH CHECK (true);

-- Remove the bootstrap USING(true) exception for Workspace.  Direct identity
-- reads now require the request scope; pre-scope session resolution happens
-- only inside the token-bound functions below.
DROP POLICY IF EXISTS workspaces_tenant_rls ON public.workspaces;
CREATE POLICY workspaces_tenant_rls ON public.workspaces
    FOR ALL TO omega_workspace, omega_mcp_infra
    USING (omega_rls_tenant_matches(tenant_id))
    WITH CHECK (omega_rls_tenant_matches(tenant_id));
DROP POLICY IF EXISTS workspaces_platform_owner_rls ON public.workspaces;
CREATE POLICY workspaces_platform_owner_rls ON public.workspaces
    FOR ALL TO omega_console, omega_refinement
    USING (true)
    WITH CHECK (true);

DROP POLICY IF EXISTS users_tenant_rls ON public.users;
CREATE POLICY users_tenant_rls ON public.users
    FOR ALL TO omega_workspace, omega_mcp_infra
    USING (omega_rls_tenant_matches(tenant_id))
    WITH CHECK (omega_rls_tenant_matches(tenant_id));
DROP POLICY IF EXISTS users_platform_owner_rls ON public.users;
CREATE POLICY users_platform_owner_rls ON public.users
    FOR ALL TO omega_console, omega_refinement
    USING (true)
    WITH CHECK (true);
DROP POLICY IF EXISTS users_auth_lookup_rls ON public.users;
CREATE POLICY users_auth_lookup_rls ON public.users
    FOR SELECT TO omega_auth
    USING (true);

DROP POLICY IF EXISTS user_workspace_roles_workspace_rls ON public.user_workspace_roles;
CREATE POLICY user_workspace_roles_workspace_rls ON public.user_workspace_roles
    FOR ALL TO omega_workspace, omega_mcp_infra
    USING (
        EXISTS (
            SELECT 1 FROM public.workspaces w
             WHERE w.id = user_workspace_roles.workspace_id
               AND omega_rls_workspace_matches(w.tenant_id, w.id)
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM public.workspaces w
             WHERE w.id = user_workspace_roles.workspace_id
               AND omega_rls_workspace_matches(w.tenant_id, w.id)
        )
    );
DROP POLICY IF EXISTS user_workspace_roles_platform_owner_rls ON public.user_workspace_roles;
CREATE POLICY user_workspace_roles_platform_owner_rls ON public.user_workspace_roles
    FOR ALL TO omega_console, omega_refinement
    USING (true)
    WITH CHECK (true);
DROP POLICY IF EXISTS user_workspace_roles_auth_lookup_rls ON public.user_workspace_roles;
CREATE POLICY user_workspace_roles_auth_lookup_rls ON public.user_workspace_roles
    FOR SELECT TO omega_auth
    USING (true);

DROP POLICY IF EXISTS workspaces_auth_lookup_rls ON public.workspaces;
CREATE POLICY workspaces_auth_lookup_rls ON public.workspaces
    FOR SELECT TO omega_auth
    USING (true);

REVOKE ALL ON public.user_sessions, public.refresh_tokens
    FROM PUBLIC, omega_console, omega_workspace;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.user_sessions, public.refresh_tokens
    TO omega_auth;
GRANT USAGE, SELECT ON SEQUENCE public.refresh_tokens_id_seq TO omega_auth;
GRANT SELECT (id, email, name, role, is_active, must_change_password, tenant_id)
    ON public.users TO omega_auth;
GRANT SELECT ON public.user_workspace_roles, public.workspaces, public.tenants, public.roles
    TO omega_auth;

-- Workspace may list safe assignee fields only after app.tenant_id and
-- app.workspace_id have been set.  password_hash is intentionally absent.
REVOKE SELECT ON public.users FROM omega_workspace;
GRANT SELECT (id, email, name, role, is_active, must_change_password, tenant_id)
    ON public.users TO omega_workspace;
REVOKE INSERT, UPDATE, DELETE ON public.user_workspace_roles FROM omega_workspace;

CREATE OR REPLACE FUNCTION public.omega_auth_create_session(
    p_token_hash text,
    p_user_id bigint,
    p_expires_at timestamptz,
    p_ip text
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF p_token_hash !~ '^[0-9a-f]{64}$' OR p_expires_at <= clock_timestamp() THEN
        RAISE EXCEPTION 'invalid session material' USING ERRCODE = '22023';
    END IF;
    INSERT INTO public.user_sessions (token_hash, user_id, expires_at, ip)
    VALUES (p_token_hash, p_user_id, p_expires_at, p_ip);
END
$$;

CREATE OR REPLACE FUNCTION public.omega_auth_resolve_session(
    p_token_hash text,
    p_new_expires_at timestamptz,
    p_slide_before timestamptz,
    p_created_after timestamptz
) RETURNS TABLE (
    user_id bigint,
    email text,
    name text,
    role text,
    is_active boolean,
    must_change_password boolean,
    tenant_id uuid,
    session_created_at timestamptz,
    session_expires_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF p_token_hash !~ '^[0-9a-f]{64}$' THEN
        RETURN;
    END IF;

    DELETE FROM public.user_sessions s
     WHERE s.token_hash = p_token_hash
       AND (
           s.expires_at <= clock_timestamp()
           OR s.created_at < p_created_after
           OR NOT EXISTS (
               SELECT 1 FROM public.users u
                WHERE u.id = s.user_id AND u.is_active = true
           )
       );

    RETURN QUERY
    UPDATE public.user_sessions s
       SET expires_at = CASE
               WHEN s.expires_at < p_slide_before THEN p_new_expires_at
               ELSE s.expires_at
           END,
           last_seen = clock_timestamp()
      FROM public.users u
     WHERE s.token_hash = p_token_hash
       AND s.user_id = u.id
       AND s.expires_at > clock_timestamp()
       AND s.created_at >= p_created_after
       AND u.is_active = true
    RETURNING s.user_id, u.email, u.name, u.role, u.is_active,
              u.must_change_password, u.tenant_id, s.created_at, s.expires_at;
END
$$;

CREATE OR REPLACE FUNCTION public.omega_auth_resolve_workspace_session(
    p_token_hash text,
    p_requested_workspace_id uuid,
    p_new_expires_at timestamptz,
    p_slide_before timestamptz,
    p_created_after timestamptz
) RETURNS TABLE (
    user_id bigint,
    email text,
    name text,
    role text,
    is_active boolean,
    must_change_password boolean,
    session_created_at timestamptz,
    session_expires_at timestamptz,
    workspace_id uuid,
    workspace_name text,
    tenant_id uuid,
    tenant_name text,
    workspace_role text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF p_token_hash !~ '^[0-9a-f]{64}$' THEN
        RETURN;
    END IF;

    DELETE FROM public.user_sessions s
     WHERE s.token_hash = p_token_hash
       AND (
           s.expires_at <= clock_timestamp()
           OR s.created_at < p_created_after
           OR NOT EXISTS (
               SELECT 1 FROM public.users u
                WHERE u.id = s.user_id AND u.is_active = true
           )
       );

    RETURN QUERY
    WITH valid_session AS (
        UPDATE public.user_sessions s
           SET expires_at = CASE
                   WHEN s.expires_at < p_slide_before THEN p_new_expires_at
                   ELSE s.expires_at
               END,
               last_seen = clock_timestamp()
          FROM public.users u
         WHERE s.token_hash = p_token_hash
           AND s.user_id = u.id
           AND s.expires_at > clock_timestamp()
           AND s.created_at >= p_created_after
           AND u.is_active = true
        RETURNING s.user_id, u.email, u.name, u.role, u.is_active,
                  u.must_change_password, s.created_at, s.expires_at
    )
    SELECT v.user_id, v.email, v.name, v.role, v.is_active,
           v.must_change_password, v.created_at, v.expires_at,
           w.id, w.name, t.id, t.name, r.name
      FROM valid_session v
      JOIN public.user_workspace_roles uwr ON uwr.user_id = v.user_id
      JOIN public.workspaces w ON w.id = uwr.workspace_id
      JOIN public.tenants t ON t.id = w.tenant_id
      JOIN public.roles r ON r.id = uwr.role_id
     WHERE p_requested_workspace_id IS NULL OR w.id = p_requested_workspace_id
     ORDER BY w.created_at ASC, w.name ASC, r.name ASC;
END
$$;

CREATE OR REPLACE FUNCTION public.omega_auth_destroy_session(p_token_hash text)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    deleted_count integer;
BEGIN
    IF p_token_hash !~ '^[0-9a-f]{64}$' THEN
        RETURN false;
    END IF;
    DELETE FROM public.user_sessions WHERE token_hash = p_token_hash;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count = 1;
END
$$;

CREATE OR REPLACE FUNCTION public.omega_auth_cleanup_sessions()
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    deleted_count integer;
BEGIN
    DELETE FROM public.user_sessions WHERE expires_at < clock_timestamp();
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END
$$;

CREATE OR REPLACE FUNCTION public.omega_auth_revoke_user_tokens(p_user_id bigint)
RETURNS integer
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    WITH deleted_sessions AS (
        DELETE FROM public.user_sessions WHERE user_id = p_user_id RETURNING 1
    ), deleted_refresh AS (
        DELETE FROM public.refresh_tokens WHERE user_id = p_user_id RETURNING 1
    )
    SELECT (SELECT count(*) FROM deleted_sessions)::integer
         + (SELECT count(*) FROM deleted_refresh)::integer
$$;

CREATE OR REPLACE FUNCTION public.omega_auth_list_sessions(
    p_requester_user_id bigint,
    p_platform_admin boolean,
    p_workspace_ids uuid[]
) RETURNS TABLE (
    session_id text,
    user_id bigint,
    user_email text,
    ip text,
    last_seen timestamptz,
    user_agent text,
    created_at timestamptz,
    expires_at timestamptz
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT s.token_hash::text, s.user_id, u.email, s.ip, s.last_seen,
           s.user_agent, s.created_at, s.expires_at
      FROM public.user_sessions s
      JOIN public.users u ON u.id = s.user_id
     WHERE p_platform_admin
        OR s.user_id = p_requester_user_id
        OR (
            u.role NOT IN ('owner', 'super_admin', 'admin')
            AND EXISTS (
                SELECT 1 FROM public.user_workspace_roles uwr
                 WHERE uwr.user_id = s.user_id
                   AND uwr.workspace_id = ANY(COALESCE(p_workspace_ids, ARRAY[]::uuid[]))
            )
        )
     ORDER BY s.last_seen DESC NULLS LAST, s.created_at DESC
$$;

CREATE OR REPLACE FUNCTION public.omega_auth_revoke_session_by_id(
    p_session_id text,
    p_requester_user_id bigint,
    p_platform_admin boolean,
    p_workspace_ids uuid[]
) RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    deleted_count integer;
BEGIN
    IF p_session_id !~ '^[0-9a-f]{64}$' THEN
        RETURN false;
    END IF;
    DELETE FROM public.user_sessions s
    USING public.users u
     WHERE s.token_hash = p_session_id
       AND u.id = s.user_id
       AND (
           p_platform_admin
           OR s.user_id = p_requester_user_id
           OR (
               u.role NOT IN ('owner', 'super_admin', 'admin')
               AND EXISTS (
                   SELECT 1 FROM public.user_workspace_roles uwr
                    WHERE uwr.user_id = s.user_id
                      AND uwr.workspace_id = ANY(COALESCE(p_workspace_ids, ARRAY[]::uuid[]))
               )
           )
       );
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count = 1;
END
$$;

CREATE OR REPLACE FUNCTION public.omega_auth_create_refresh_token(
    p_user_id bigint,
    p_token_hash text,
    p_expires_at timestamptz
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF p_token_hash !~ '^[0-9a-f]{64}$' OR p_expires_at <= clock_timestamp() THEN
        RAISE EXCEPTION 'invalid refresh material' USING ERRCODE = '22023';
    END IF;
    INSERT INTO public.refresh_tokens (user_id, token_hash, expires_at)
    SELECT u.id, p_token_hash, p_expires_at
      FROM public.users u
     WHERE u.id = p_user_id AND u.is_active = true;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'active refresh subject not found' USING ERRCODE = '23503';
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION public.omega_auth_get_refresh_user(p_token_hash text)
RETURNS TABLE (
    user_id bigint,
    email text,
    name text,
    role text,
    is_active boolean,
    must_change_password boolean,
    tenant_id uuid
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT rt.user_id, u.email, u.name, u.role, u.is_active,
           u.must_change_password, u.tenant_id
      FROM public.refresh_tokens rt
      JOIN public.users u ON u.id = rt.user_id
     WHERE p_token_hash ~ '^[0-9a-f]{64}$'
       AND rt.token_hash = p_token_hash
       AND rt.revoked_at IS NULL
       AND rt.expires_at > clock_timestamp()
       AND u.is_active = true
$$;

CREATE OR REPLACE FUNCTION public.omega_auth_revoke_refresh_token(p_token_hash text)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    updated_count integer;
BEGIN
    IF p_token_hash !~ '^[0-9a-f]{64}$' THEN
        RETURN false;
    END IF;
    UPDATE public.refresh_tokens
       SET revoked_at = clock_timestamp()
     WHERE token_hash = p_token_hash AND revoked_at IS NULL;
    GET DIAGNOSTICS updated_count = ROW_COUNT;
    RETURN updated_count = 1;
END
$$;

-- One SQL statement consumes the old token and creates its successor.  A
-- duplicate caller observes zero rows in consumed and therefore inserts
-- nothing.  A duplicate new hash aborts the statement, rolling consumption
-- back with it.
CREATE OR REPLACE FUNCTION public.omega_auth_rotate_refresh_token(
    p_old_token_hash text,
    p_new_token_hash text,
    p_new_expires_at timestamptz
) RETURNS TABLE (
    user_id bigint,
    email text,
    name text,
    role text,
    is_active boolean,
    must_change_password boolean,
    tenant_id uuid
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    WITH consumed AS (
        UPDATE public.refresh_tokens rt
           SET revoked_at = clock_timestamp()
          FROM public.users subject
         WHERE p_old_token_hash ~ '^[0-9a-f]{64}$'
           AND p_new_token_hash ~ '^[0-9a-f]{64}$'
           AND p_new_expires_at > clock_timestamp()
           AND rt.token_hash = p_old_token_hash
           AND rt.revoked_at IS NULL
           AND rt.expires_at > clock_timestamp()
           AND subject.id = rt.user_id
           AND subject.is_active = true
        RETURNING rt.user_id
    ), inserted AS (
        INSERT INTO public.refresh_tokens (user_id, token_hash, expires_at)
        SELECT consumed.user_id, p_new_token_hash, p_new_expires_at
          FROM consumed
        RETURNING user_id
    )
    SELECT u.id, u.email, u.name, u.role, u.is_active,
           u.must_change_password, u.tenant_id
      FROM consumed c
      JOIN inserted i ON i.user_id = c.user_id
      JOIN public.users u ON u.id = c.user_id
$$;

ALTER FUNCTION public.omega_auth_create_session(text, bigint, timestamptz, text) OWNER TO omega_auth;
ALTER FUNCTION public.omega_auth_resolve_session(text, timestamptz, timestamptz, timestamptz) OWNER TO omega_auth;
ALTER FUNCTION public.omega_auth_resolve_workspace_session(text, uuid, timestamptz, timestamptz, timestamptz) OWNER TO omega_auth;
ALTER FUNCTION public.omega_auth_destroy_session(text) OWNER TO omega_auth;
ALTER FUNCTION public.omega_auth_cleanup_sessions() OWNER TO omega_auth;
ALTER FUNCTION public.omega_auth_revoke_user_tokens(bigint) OWNER TO omega_auth;
ALTER FUNCTION public.omega_auth_list_sessions(bigint, boolean, uuid[]) OWNER TO omega_auth;
ALTER FUNCTION public.omega_auth_revoke_session_by_id(text, bigint, boolean, uuid[]) OWNER TO omega_auth;
ALTER FUNCTION public.omega_auth_create_refresh_token(bigint, text, timestamptz) OWNER TO omega_auth;
ALTER FUNCTION public.omega_auth_get_refresh_user(text) OWNER TO omega_auth;
ALTER FUNCTION public.omega_auth_revoke_refresh_token(text) OWNER TO omega_auth;
ALTER FUNCTION public.omega_auth_rotate_refresh_token(text, text, timestamptz) OWNER TO omega_auth;

REVOKE ALL ON FUNCTION public.omega_auth_create_session(text, bigint, timestamptz, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.omega_auth_resolve_session(text, timestamptz, timestamptz, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.omega_auth_resolve_workspace_session(text, uuid, timestamptz, timestamptz, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.omega_auth_destroy_session(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.omega_auth_cleanup_sessions() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.omega_auth_revoke_user_tokens(bigint) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.omega_auth_list_sessions(bigint, boolean, uuid[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.omega_auth_revoke_session_by_id(text, bigint, boolean, uuid[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.omega_auth_create_refresh_token(bigint, text, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.omega_auth_get_refresh_user(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.omega_auth_revoke_refresh_token(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.omega_auth_rotate_refresh_token(text, text, timestamptz) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION public.omega_auth_create_session(text, bigint, timestamptz, text) TO omega_console;
GRANT EXECUTE ON FUNCTION public.omega_auth_resolve_session(text, timestamptz, timestamptz, timestamptz) TO omega_console;
GRANT EXECUTE ON FUNCTION public.omega_auth_resolve_workspace_session(text, uuid, timestamptz, timestamptz, timestamptz) TO omega_workspace;
GRANT EXECUTE ON FUNCTION public.omega_auth_destroy_session(text) TO omega_console, omega_workspace;
GRANT EXECUTE ON FUNCTION public.omega_auth_cleanup_sessions() TO omega_console;
GRANT EXECUTE ON FUNCTION public.omega_auth_revoke_user_tokens(bigint) TO omega_console;
GRANT EXECUTE ON FUNCTION public.omega_auth_list_sessions(bigint, boolean, uuid[]) TO omega_console;
GRANT EXECUTE ON FUNCTION public.omega_auth_revoke_session_by_id(text, bigint, boolean, uuid[]) TO omega_console;
GRANT EXECUTE ON FUNCTION public.omega_auth_create_refresh_token(bigint, text, timestamptz) TO omega_console;
GRANT EXECUTE ON FUNCTION public.omega_auth_get_refresh_user(text) TO omega_console;
GRANT EXECUTE ON FUNCTION public.omega_auth_revoke_refresh_token(text) TO omega_console;
GRANT EXECUTE ON FUNCTION public.omega_auth_rotate_refresh_token(text, text, timestamptz) TO omega_console;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zzy_identity_session_boundary.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
