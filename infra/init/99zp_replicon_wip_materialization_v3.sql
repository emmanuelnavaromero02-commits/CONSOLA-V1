-- Durable provenance and fail-closed publication for Replicon WIP v3.

ALTER TABLE kb_config ADD COLUMN IF NOT EXISTS package_version TEXT;
ALTER TABLE kb_config ADD COLUMN IF NOT EXISTS package_sql_digest TEXT;
ALTER TABLE kb_config ADD COLUMN IF NOT EXISTS materialization_status TEXT;
ALTER TABLE kb_config ADD COLUMN IF NOT EXISTS current_run_id TEXT;
ALTER TABLE kb_config ADD COLUMN IF NOT EXISTS invalid_reason TEXT;

ALTER TABLE kb_runs ADD COLUMN IF NOT EXISTS cartridge_id TEXT;
ALTER TABLE kb_runs ADD COLUMN IF NOT EXISTS package_version TEXT;
ALTER TABLE kb_runs ADD COLUMN IF NOT EXISTS sql_digest TEXT;
ALTER TABLE kb_runs ADD COLUMN IF NOT EXISTS input_digest TEXT;
ALTER TABLE kb_runs ADD COLUMN IF NOT EXISTS generation BIGINT;
ALTER TABLE kb_runs ADD COLUMN IF NOT EXISTS artifact_status TEXT;
ALTER TABLE kb_runs ADD COLUMN IF NOT EXISTS invalid_reason TEXT;

CREATE TABLE IF NOT EXISTS kb_materialization_heads (
    cartridge_id TEXT NOT NULL,
    kb_id TEXT NOT NULL,
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    current_run_id TEXT NOT NULL REFERENCES kb_runs(run_id) ON DELETE RESTRICT,
    package_version TEXT NOT NULL,
    sql_digest TEXT NOT NULL,
    input_digest TEXT NOT NULL,
    generation BIGINT NOT NULL,
    state TEXT NOT NULL CHECK (state = 'current'),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (cartridge_id, kb_id, tenant_id, workspace_id)
);

UPDATE kb_config
   SET materialization_status = 'quarantined',
       current_run_id = NULL,
       invalid_reason = 'invalid_legacy_fx'
 WHERE cartridge_id = 'replicon'
   AND (
       (kb_id = 'kb_wip_mensual'
        AND package_version IS DISTINCT FROM 'replicon.wip_mensual.v3')
       OR (kb_id = 'kb_wip_resumen'
        AND package_version IS DISTINCT FROM 'replicon.wip_resumen.v3')
   );

UPDATE kb_runs
   SET artifact_status = 'legacy_invalid',
       invalid_reason = 'invalid_legacy_fx'
 WHERE kb_id IN ('kb_wip_mensual', 'kb_wip_resumen')
   AND COALESCE(cartridge_id, 'replicon') = 'replicon'
   AND COALESCE(package_version, '') NOT IN (
       'replicon.wip_mensual.v3', 'replicon.wip_resumen.v3'
   );

DELETE FROM kb_materialization_heads
 WHERE cartridge_id = 'replicon'
   AND (
       (kb_id = 'kb_wip_mensual' AND package_version != 'replicon.wip_mensual.v3')
       OR (kb_id = 'kb_wip_resumen' AND package_version != 'replicon.wip_resumen.v3')
   );

CREATE SCHEMA IF NOT EXISTS knowledge_bits;
CREATE SCHEMA IF NOT EXISTS knowledge_bits_history;
CREATE SCHEMA IF NOT EXISTS knowledge_bits_quarantine;
REVOKE ALL ON SCHEMA knowledge_bits_history, knowledge_bits_quarantine FROM PUBLIC;
REVOKE ALL ON SCHEMA knowledge_bits_quarantine FROM omega_cartridge_replicon;
GRANT USAGE, CREATE ON SCHEMA knowledge_bits, knowledge_bits_history
    TO omega_cartridge_replicon;

DO $quarantine$
DECLARE
    table_name TEXT;
    target_name TEXT;
    source_oid OID;
BEGIN
    FOR table_name IN
        SELECT physical_name FROM (
            VALUES ('replicon_wip_mensual'), ('replicon_wip_resumen')
        ) defaults(physical_name)
        UNION
        SELECT pg_table FROM kb_config
         WHERE cartridge_id = 'replicon'
           AND kb_id IN ('kb_wip_mensual', 'kb_wip_resumen')
           AND package_version IS DISTINCT FROM CASE kb_id
               WHEN 'kb_wip_mensual' THEN 'replicon.wip_mensual.v3'
               ELSE 'replicon.wip_resumen.v3'
           END
           AND pg_table IS NOT NULL
    LOOP
        SELECT relation.oid INTO source_oid
          FROM pg_class relation JOIN pg_namespace namespace
            ON namespace.oid = relation.relnamespace
         WHERE namespace.nspname = 'knowledge_bits'
           AND relation.relname = table_name
           AND relation.relkind IN ('r', 'p', 'm', 'f');
        IF source_oid IS NOT NULL THEN
            target_name := table_name;
            IF to_regclass(format('knowledge_bits_quarantine.%I', target_name)) IS NOT NULL THEN
                target_name := LEFT(table_name, 40) || '_legacy_' || source_oid::TEXT;
                EXECUTE format(
                    'ALTER TABLE knowledge_bits.%I RENAME TO %I', table_name, target_name
                );
            END IF;
            EXECUTE format(
                'ALTER TABLE knowledge_bits.%I SET SCHEMA knowledge_bits_quarantine', target_name
            );
        END IF;
        source_oid := NULL;
    END LOOP;
END
$quarantine$;

CREATE TABLE IF NOT EXISTS replicon_base_currency_config (
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    effective_from DATE NOT NULL,
    effective_to DATE,
    currency TEXT NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    authority_source TEXT NOT NULL,
    verified_by BIGINT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    verified_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, workspace_id, effective_from),
    CHECK (effective_to IS NULL OR effective_to > effective_from)
);

ALTER TABLE kb_materialization_heads ENABLE ROW LEVEL SECURITY;
ALTER TABLE kb_materialization_heads FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS kb_materialization_heads_workspace_rls ON kb_materialization_heads;
CREATE POLICY kb_materialization_heads_workspace_rls ON kb_materialization_heads
    USING (omega_rls_workspace_matches(tenant_id, workspace_id))
    WITH CHECK (omega_rls_workspace_matches(tenant_id, workspace_id));

ALTER TABLE replicon_base_currency_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE replicon_base_currency_config FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS replicon_base_currency_workspace_rls ON replicon_base_currency_config;
CREATE POLICY replicon_base_currency_workspace_rls ON replicon_base_currency_config
    USING (omega_rls_workspace_matches(tenant_id, workspace_id))
    WITH CHECK (omega_rls_workspace_matches(tenant_id, workspace_id));

GRANT SELECT, INSERT, UPDATE ON kb_materialization_heads TO omega_cartridge_replicon;
GRANT SELECT ON replicon_base_currency_config TO omega_cartridge_replicon;

INSERT INTO schema_migrations(filename, applied_at)
VALUES ('99zp_replicon_wip_materialization_v3.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
