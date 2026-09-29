-- v1.45.267 — personal access tokens for AI assistants (hash-only, workspace-bound).

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS public.user_access_tokens (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         bigint NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    tenant_id       uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    workspace_id    uuid NOT NULL REFERENCES public.workspaces(id) ON DELETE CASCADE,
    name            text NOT NULL,
    token_hash      varchar(64) NOT NULL,
    token_prefix    text NOT NULL,
    scopes          text[] NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at      timestamptz NOT NULL,
    last_used_at    timestamptz,
    last_used_ip    text,
    revoked_at      timestamptz,
    revoked_reason  text,
    CONSTRAINT user_access_tokens_token_hash_key UNIQUE (token_hash),
    CONSTRAINT user_access_tokens_name_check
        CHECK (char_length(name) BETWEEN 1 AND 80),
    CONSTRAINT user_access_tokens_token_hash_shape
        CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT user_access_tokens_token_prefix_shape
        CHECK (token_prefix ~ '^omega_pat_[A-Za-z0-9]{4}$'),
    CONSTRAINT user_access_tokens_scopes_check
        CHECK (cardinality(scopes) >= 1 AND scopes <@ ARRAY['lectura', 'acciones']::text[]),
    CONSTRAINT user_access_tokens_expiry_check
        CHECK (expires_at > created_at AND expires_at <= created_at + interval '90 days'),
    CONSTRAINT user_access_tokens_last_used_ip_check
        CHECK (last_used_ip IS NULL OR char_length(last_used_ip) <= 64),
    CONSTRAINT user_access_tokens_revoked_reason_check
        CHECK (
            revoked_reason IS NULL
            OR revoked_reason IN ('usuario', 'administrador', 'credenciales_restablecidas')
        ),
    CONSTRAINT user_access_tokens_revocation_pair_check
        CHECK ((revoked_at IS NULL) = (revoked_reason IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_user_access_tokens_active_user
    ON public.user_access_tokens (user_id)
    WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_user_access_tokens_workspace
    ON public.user_access_tokens (workspace_id);

ALTER TABLE public.user_access_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_access_tokens FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS user_access_tokens_auth_boundary_rls ON public.user_access_tokens;
CREATE POLICY user_access_tokens_auth_boundary_rls ON public.user_access_tokens
    FOR ALL TO omega_auth
    USING (true)
    WITH CHECK (true);

DO $$
DECLARE
    service_role text;
BEGIN
    REVOKE ALL ON public.user_access_tokens FROM PUBLIC;
    FOREACH service_role IN ARRAY ARRAY[
        'omega_console', 'omega_workspace', 'omega_refinement', 'omega_mcp_infra'
    ] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = service_role) THEN
            EXECUTE format('REVOKE ALL ON public.user_access_tokens FROM %I', service_role);
        END IF;
    END LOOP;
END $$;

GRANT SELECT, INSERT, UPDATE, DELETE ON public.user_access_tokens TO omega_auth;

CREATE OR REPLACE FUNCTION public.omega_auth_create_access_token(
    p_user_id bigint,
    p_workspace_id uuid,
    p_name text,
    p_token_hash text,
    p_token_prefix text,
    p_scopes text[],
    p_expires_at timestamptz
) RETURNS TABLE (
    token_id uuid,
    tenant_id uuid,
    workspace_id uuid,
    token_name text,
    token_prefix text,
    scopes text[],
    created_at timestamptz,
    expires_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
#variable_conflict use_column
DECLARE
    policy_now constant timestamptz := clock_timestamp();
    policy_max_expiry constant timestamptz := policy_now + interval '90 days';
    clean_name text := btrim(coalesce(p_name, ''));
    clean_scopes text[];
    subject_role text;
    subject_active boolean;
    subject_must_change boolean;
    bound_tenant uuid;
    active_count integer;
BEGIN
    IF p_token_hash IS NULL OR p_token_hash !~ '^[0-9a-f]{64}$'
       OR p_token_prefix IS NULL OR p_token_prefix !~ '^omega_pat_[A-Za-z0-9]{4}$'
       OR char_length(clean_name) NOT BETWEEN 1 AND 80
       OR p_scopes IS NULL OR cardinality(p_scopes) < 1
       OR NOT (p_scopes <@ ARRAY['lectura', 'acciones']::text[])
       OR p_expires_at IS NULL
       OR p_expires_at <= policy_now + interval '1 hour'
       OR p_expires_at > policy_max_expiry + interval '5 minutes' THEN
        RAISE EXCEPTION 'invalid access token material' USING ERRCODE = '22023';
    END IF;

    PERFORM pg_advisory_xact_lock(hashtext('omega_pat:' || p_user_id::text));

    SELECT u.role, u.is_active, u.must_change_password
      INTO subject_role, subject_active, subject_must_change
      FROM public.users u
     WHERE u.id = p_user_id;
    IF NOT FOUND OR subject_active IS NOT TRUE OR subject_must_change IS TRUE THEN
        RAISE EXCEPTION 'access token subject is not eligible' USING ERRCODE = '42501';
    END IF;

    SELECT w.tenant_id INTO bound_tenant
      FROM public.workspaces w
     WHERE w.id = p_workspace_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'access token workspace is not available' USING ERRCODE = '42501';
    END IF;

    IF coalesce(subject_role, '') NOT IN ('owner', 'super_admin', 'admin')
       AND NOT EXISTS (
           SELECT 1
             FROM public.user_workspace_roles uwr
            WHERE uwr.user_id = p_user_id
              AND uwr.workspace_id = p_workspace_id
       ) THEN
        RAISE EXCEPTION 'access token workspace is not available' USING ERRCODE = '42501';
    END IF;

    SELECT count(*) INTO active_count
      FROM public.user_access_tokens t
     WHERE t.user_id = p_user_id
       AND t.revoked_at IS NULL
       AND t.expires_at > policy_now;
    IF active_count >= 10 THEN
        RAISE EXCEPTION 'active access token limit reached' USING ERRCODE = '53400';
    END IF;

    SELECT array_agg(DISTINCT s ORDER BY s) INTO clean_scopes
      FROM unnest(p_scopes) AS s;

    RETURN QUERY
    INSERT INTO public.user_access_tokens AS t (
        user_id, tenant_id, workspace_id, name, token_hash, token_prefix,
        scopes, created_at, expires_at
    )
    VALUES (
        p_user_id, bound_tenant, p_workspace_id, clean_name, p_token_hash,
        p_token_prefix, clean_scopes, policy_now,
        LEAST(p_expires_at, policy_max_expiry)
    )
    RETURNING t.id, t.tenant_id, t.workspace_id, t.name, t.token_prefix,
              t.scopes, t.created_at, t.expires_at;
END
$$;

CREATE OR REPLACE FUNCTION public.omega_auth_resolve_access_token(
    p_token_hash text,
    p_ip text
) RETURNS TABLE (
    token_id uuid,
    user_id bigint,
    email text,
    user_name text,
    role text,
    user_tenant_id uuid,
    tenant_id uuid,
    workspace_id uuid,
    workspace_name text,
    token_name text,
    token_prefix text,
    scopes text[],
    expires_at timestamptz,
    status text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
#variable_conflict use_column
DECLARE
    policy_now constant timestamptz := clock_timestamp();
    found_token public.user_access_tokens%ROWTYPE;
    subject_email text;
    subject_name text;
    subject_role text;
    subject_tenant uuid;
    subject_active boolean;
    subject_must_change boolean;
    bound_workspace_name text;
    resolved_status text;
BEGIN
    IF p_token_hash IS NULL OR p_token_hash !~ '^[0-9a-f]{64}$' THEN
        RETURN;
    END IF;

    SELECT * INTO found_token
      FROM public.user_access_tokens t
     WHERE t.token_hash = p_token_hash;
    IF NOT FOUND THEN
        RETURN;
    END IF;

    SELECT u.email, u.name, u.role, u.tenant_id, u.is_active, u.must_change_password
      INTO subject_email, subject_name, subject_role, subject_tenant,
           subject_active, subject_must_change
      FROM public.users u
     WHERE u.id = found_token.user_id;

    SELECT w.name INTO bound_workspace_name
      FROM public.workspaces w
     WHERE w.id = found_token.workspace_id
       AND w.tenant_id = found_token.tenant_id;

    resolved_status := CASE
        WHEN found_token.revoked_at IS NOT NULL THEN 'revocado'
        WHEN found_token.expires_at <= policy_now THEN 'vencido'
        WHEN subject_active IS NOT TRUE THEN 'usuario_inactivo'
        WHEN subject_must_change IS TRUE THEN 'cambio_de_contrasena'
        WHEN bound_workspace_name IS NULL THEN 'espacio_inexistente'
        ELSE 'activo'
    END;

    IF resolved_status = 'activo' THEN
        UPDATE public.user_access_tokens t
           SET last_used_at = policy_now,
               last_used_ip = left(nullif(btrim(coalesce(p_ip, '')), ''), 64)
         WHERE t.id = found_token.id
           AND (t.last_used_at IS NULL OR t.last_used_at < policy_now - interval '5 minutes');
    END IF;

    token_id := found_token.id;
    user_id := found_token.user_id;
    email := subject_email;
    user_name := subject_name;
    role := subject_role;
    user_tenant_id := subject_tenant;
    tenant_id := found_token.tenant_id;
    workspace_id := found_token.workspace_id;
    workspace_name := bound_workspace_name;
    token_name := found_token.name;
    token_prefix := found_token.token_prefix;
    scopes := found_token.scopes;
    expires_at := found_token.expires_at;
    status := resolved_status;
    RETURN NEXT;
END
$$;

CREATE OR REPLACE FUNCTION public.omega_auth_list_access_tokens(p_user_id bigint)
RETURNS TABLE (
    token_id uuid,
    workspace_id uuid,
    workspace_name text,
    token_name text,
    token_prefix text,
    scopes text[],
    created_at timestamptz,
    expires_at timestamptz,
    last_used_at timestamptz,
    revoked_at timestamptz,
    revoked_reason text,
    status text
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT t.id, t.workspace_id, w.name, t.name, t.token_prefix, t.scopes,
           t.created_at, t.expires_at, t.last_used_at, t.revoked_at,
           t.revoked_reason,
           CASE
               WHEN t.revoked_at IS NOT NULL THEN 'revocado'
               WHEN t.expires_at <= clock_timestamp() THEN 'vencido'
               WHEN u.is_active IS NOT TRUE
                    OR w.tenant_id IS DISTINCT FROM t.tenant_id
                    OR (
                        coalesce(u.role, '') NOT IN ('owner', 'super_admin', 'admin')
                        AND NOT EXISTS (
                            SELECT 1
                              FROM public.user_workspace_roles uwr
                             WHERE uwr.user_id = t.user_id
                               AND uwr.workspace_id = t.workspace_id
                        )
                    ) THEN 'sin_acceso'
               ELSE 'activo'
           END
      FROM public.user_access_tokens t
      JOIN public.users u ON u.id = t.user_id
      LEFT JOIN public.workspaces w ON w.id = t.workspace_id
     WHERE t.user_id = p_user_id
     ORDER BY (t.revoked_at IS NULL AND t.expires_at > clock_timestamp()) DESC,
              t.created_at DESC
     LIMIT 100
$$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM pg_proc
         WHERE oid = to_regprocedure('public.omega_auth_revoke_access_token(bigint, uuid)')
           AND prorettype = 'boolean'::regtype
    ) THEN
        DROP FUNCTION public.omega_auth_revoke_access_token(bigint, uuid);
    END IF;
END $$;

CREATE OR REPLACE FUNCTION public.omega_auth_revoke_access_token(
    p_user_id bigint,
    p_token_id uuid
) RETURNS TABLE (
    token_id uuid,
    workspace_id uuid,
    token_prefix text,
    scopes text[],
    expires_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
#variable_conflict use_column
BEGIN
    RETURN QUERY
    UPDATE public.user_access_tokens t
       SET revoked_at = clock_timestamp(),
           revoked_reason = 'usuario'
     WHERE t.id = p_token_id
       AND t.user_id = p_user_id
       AND t.revoked_at IS NULL
    RETURNING t.id, t.workspace_id, t.token_prefix, t.scopes, t.expires_at;
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
    ), revoked_access AS (
        UPDATE public.user_access_tokens
           SET revoked_at = clock_timestamp(),
               revoked_reason = 'credenciales_restablecidas'
         WHERE user_id = p_user_id
           AND revoked_at IS NULL
        RETURNING 1
    )
    SELECT (SELECT count(*) FROM deleted_sessions)::integer
         + (SELECT count(*) FROM deleted_refresh)::integer
         + (SELECT count(*) FROM revoked_access)::integer
$$;

ALTER FUNCTION public.omega_auth_create_access_token(bigint, uuid, text, text, text, text[], timestamptz) OWNER TO omega_auth;
ALTER FUNCTION public.omega_auth_resolve_access_token(text, text) OWNER TO omega_auth;
ALTER FUNCTION public.omega_auth_list_access_tokens(bigint) OWNER TO omega_auth;
ALTER FUNCTION public.omega_auth_revoke_access_token(bigint, uuid) OWNER TO omega_auth;
ALTER FUNCTION public.omega_auth_revoke_user_tokens(bigint) OWNER TO omega_auth;

REVOKE ALL ON FUNCTION public.omega_auth_create_access_token(bigint, uuid, text, text, text, text[], timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.omega_auth_resolve_access_token(text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.omega_auth_list_access_tokens(bigint) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.omega_auth_revoke_access_token(bigint, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.omega_auth_revoke_user_tokens(bigint) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION public.omega_auth_create_access_token(bigint, uuid, text, text, text, text[], timestamptz) TO omega_console;
GRANT EXECUTE ON FUNCTION public.omega_auth_resolve_access_token(text, text) TO omega_console;
GRANT EXECUTE ON FUNCTION public.omega_auth_list_access_tokens(bigint) TO omega_console;
GRANT EXECUTE ON FUNCTION public.omega_auth_revoke_access_token(bigint, uuid) TO omega_console;
GRANT EXECUTE ON FUNCTION public.omega_auth_revoke_user_tokens(bigint) TO omega_console;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zzzzzzb_personal_access_tokens.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
