-- 99zp_sap_successfactors_tenant_aliases.sql
--
-- Tenant/workspace scoped mapping for SuccessFactors Talent custom/MDF entity
-- aliases. This lets metadata readiness and extract_all use the live OData
-- entity name while preserving the canonical Studio entity contract.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS sap_successfactors_tenant_entity_aliases (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cartridge_id    TEXT NOT NULL DEFAULT 'sap_successfactors',
    tenant_id       UUID REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id    UUID REFERENCES workspaces(id) ON DELETE CASCADE,
    component       TEXT NOT NULL,
    group_name      TEXT,
    canonical_entity TEXT NOT NULL,
    odata_entity    TEXT NOT NULL,
    primary_key     TEXT,
    watermark_field TEXT,
    required_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
    optional_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
    select_fields   JSONB NOT NULL DEFAULT '[]'::jsonb,
    field_aliases   JSONB NOT NULL DEFAULT '{}'::jsonb,
    source          TEXT NOT NULL DEFAULT 'tenant_config',
    approved        BOOLEAN NOT NULL DEFAULT TRUE,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT sap_sf_talent_alias_component_chk CHECK (component <> ''),
    CONSTRAINT sap_sf_talent_alias_canonical_chk CHECK (canonical_entity <> ''),
    CONSTRAINT sap_sf_talent_alias_odata_chk CHECK (odata_entity <> '')
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_sap_sf_talent_alias_scope
    ON sap_successfactors_tenant_entity_aliases (
        cartridge_id,
        COALESCE(tenant_id::TEXT, ''),
        COALESCE(workspace_id::TEXT, ''),
        component,
        canonical_entity,
        odata_entity
    );

CREATE INDEX IF NOT EXISTS idx_sap_sf_talent_alias_workspace
    ON sap_successfactors_tenant_entity_aliases (
        workspace_id,
        component,
        enabled,
        approved
    );

CREATE INDEX IF NOT EXISTS idx_sap_sf_talent_alias_tenant
    ON sap_successfactors_tenant_entity_aliases (
        tenant_id,
        component,
        enabled,
        approved
    );

INSERT INTO applied_migrations (filename, applied_at)
VALUES ('99zp_sap_successfactors_tenant_aliases.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
