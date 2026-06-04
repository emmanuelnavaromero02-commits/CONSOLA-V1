-- HubSpot cartridge runtime closure.
--
-- This migration makes HubSpot a first-class built-in cartridge: dedicated
-- database role, marketplace product, DAG/entity seed, semantic terms and
-- cartridge agents. It is intentionally idempotent for fresh and upgraded
-- installations.

DO $$
DECLARE
  pw TEXT := current_setting('app.omega_cartridge_hubspot_password', true);
BEGIN
  IF pw IS NULL OR pw = '' THEN
    RAISE EXCEPTION 'app.omega_cartridge_hubspot_password not set; refusing to create role with empty password';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_cartridge_hubspot') THEN
    EXECUTE format('CREATE ROLE omega_cartridge_hubspot LOGIN PASSWORD %L', pw);
  END IF;
END $$;

GRANT CONNECT ON DATABASE modecissions TO omega_cartridge_hubspot;
GRANT USAGE ON SCHEMA public TO omega_cartridge_hubspot;
GRANT CREATE ON SCHEMA public TO omega_cartridge_hubspot;
GRANT SELECT, INSERT, UPDATE ON
    cartridges, cartridge_dags, entity_config, semantic_terms, agents,
    kb_config, entity_watermarks, extraction_runs, kb_runs, jobs, run_logs
    TO omega_cartridge_hubspot;
GRANT SELECT ON
    mcp_servers, mcp_custom_tools, marketplace_products, tenant_entitlements,
    cartridge_installations
    TO omega_cartridge_hubspot;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO omega_cartridge_hubspot;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_cartridge_jobs_owner') THEN
    GRANT omega_cartridge_jobs_owner TO omega_cartridge_hubspot;
  END IF;
END $$;

DO $$
DECLARE
  hard_locked_tables CONSTANT text[] := ARRAY[
    'users', 'user_sessions', 'user_tokens', 'refresh_tokens',
    'tenants', 'workspaces', 'roles', 'user_workspace_roles',
    'decisions', 'decision_actions',
    'audit_events', 'login_attempts', 'vault_access_log',
    'vault_entries'
  ];
  tbl text;
BEGIN
  FOREACH tbl IN ARRAY hard_locked_tables LOOP
    IF EXISTS (
      SELECT 1 FROM pg_tables
      WHERE schemaname = 'public' AND tablename = tbl
    ) THEN
      EXECUTE format(
        'REVOKE ALL PRIVILEGES ON public.%I FROM omega_cartridge_hubspot',
        tbl
      );
    END IF;
  END LOOP;
END $$;

INSERT INTO cartridges (id, name, version, description, pattern, category, bronze_path)
VALUES (
    'hubspot',
    'HubSpot CRM',
    '1.0.0',
    'HubSpot CRM connector for deals, companies, contacts, line items, owners, pipelines, weighted forecast and stale deals.',
    'dag-based',
    'cartridge',
    'raw/hubspot/{entity}/load_date={date}/'
)
ON CONFLICT (id) DO UPDATE
    SET name        = EXCLUDED.name,
        version     = EXCLUDED.version,
        description = EXCLUDED.description,
        pattern     = EXCLUDED.pattern,
        category    = EXCLUDED.category,
        bronze_path = EXCLUDED.bronze_path,
        updated_at  = NOW();

INSERT INTO marketplace_products (id, cartridge_id, name, description, category, status, sort_order, metadata)
VALUES (
    'hubspot',
    'hubspot',
    'HubSpot CRM',
    'HubSpot CRM connector for pipeline, forecast, revenue by seller and stale deal operations.',
    'cartridge',
    'active',
    60,
    jsonb_build_object('version', '1.0.0', 'pattern', 'dag-based', 'bronze_path', 'raw/hubspot/{entity}/load_date={date}/')
)
ON CONFLICT (id) DO UPDATE
    SET cartridge_id = EXCLUDED.cartridge_id,
        name         = EXCLUDED.name,
        description  = EXCLUDED.description,
        category     = EXCLUDED.category,
        status       = EXCLUDED.status,
        sort_order   = EXCLUDED.sort_order,
        metadata     = marketplace_products.metadata || EXCLUDED.metadata,
        updated_at   = NOW();

INSERT INTO cartridge_dags (cartridge_id, dag_id, file, description, trigger, params)
VALUES
    ('hubspot', 'hubspot_extract',     'hubspot_extract.py',     'Extract one HubSpot entity through the cartridge microservice.', 'on-demand', '["entity","mode"]'),
    ('hubspot', 'hubspot_extract_all', 'hubspot_extract_all.py', 'Extract all HubSpot entities through the cartridge microservice.', 'on-demand', '["mode"]')
ON CONFLICT (cartridge_id, dag_id) DO UPDATE
    SET file = EXCLUDED.file,
        description = EXCLUDED.description,
        trigger = EXCLUDED.trigger,
        params = EXCLUDED.params;

INSERT INTO entity_config
    (cartridge_id, entity, display_name, mode, primary_key, dag_id, description, watermark_field, watermark_format, page_size, enabled, trigger_type, cron_expression)
VALUES
    ('hubspot', 'deals',      'Deals',      'incremental', 'hubspot_id', 'hubspot_extract', 'Deals with amount, stage, pipeline, probability, close date and owner.', 'hs_lastmodifieddate', 'iso8601', 100, TRUE, 'scheduled', '0 0,4,8,12,16,20 * * *'),
    ('hubspot', 'companies',  'Companies',  'incremental', 'hubspot_id', 'hubspot_extract', 'Companies/accounts with industry, domain and owner.',                  'hs_lastmodifieddate', 'iso8601', 100, TRUE, 'scheduled', '0 0,6,12,18 * * *'),
    ('hubspot', 'contacts',   'Contacts',   'incremental', 'hubspot_id', 'hubspot_extract', 'Contacts with email, company, title and lifecycle stage.',              'lastmodifieddate',    'iso8601', 100, TRUE, 'scheduled', '0 0,6,12,18 * * *'),
    ('hubspot', 'line_items', 'Line Items', 'incremental', 'hubspot_id', 'hubspot_extract', 'Deal line items with product, quantity and price.',                    'hs_lastmodifieddate', 'iso8601', 100, TRUE, 'scheduled', '0 0,6,12,18 * * *'),
    ('hubspot', 'owners',     'Owners',     'full',        'hubspot_id', 'hubspot_extract', 'HubSpot owners and sellers.',                                           NULL,                  NULL,      100, TRUE, 'scheduled', '0 6 * * *'),
    ('hubspot', 'pipelines',  'Pipelines',  'full',        'stage_id',   'hubspot_extract', 'Deal pipelines and stages with probability metadata.',                  NULL,                  NULL,      100, TRUE, 'scheduled', '0 6 * * *')
ON CONFLICT (cartridge_id, entity) DO UPDATE
    SET display_name     = EXCLUDED.display_name,
        mode             = EXCLUDED.mode,
        primary_key      = EXCLUDED.primary_key,
        dag_id           = EXCLUDED.dag_id,
        description      = EXCLUDED.description,
        watermark_field  = EXCLUDED.watermark_field,
        watermark_format = EXCLUDED.watermark_format,
        page_size        = EXCLUDED.page_size,
        enabled          = EXCLUDED.enabled,
        trigger_type     = EXCLUDED.trigger_type,
        cron_expression  = EXCLUDED.cron_expression;

INSERT INTO semantic_terms (cartridge_id, term, definition, maps_to)
VALUES
    ('hubspot', 'weighted pipeline', 'Open deal amount multiplied by stage probability.', 'gold_forecast_mensual.pipeline_ponderado'),
    ('hubspot', 'forecast', 'Open pipeline expected to close in the selected period.', 'gold_forecast_mensual'),
    ('hubspot', 'win rate', 'Won deals divided by won plus lost deals.', 'gold_pipeline_salud'),
    ('hubspot', 'stale deal', 'Open deal without recent activity.', 'gold_deals_estancados')
ON CONFLICT (cartridge_id, term) DO UPDATE
    SET definition = EXCLUDED.definition,
        maps_to = EXCLUDED.maps_to;

INSERT INTO agents (cartridge_id, slug, name, description, instructions, personality,
                    allowed_tools, rag_filter, model, max_tokens, temperature, extra)
VALUES (
    'hubspot',
    'forecast_watchdog',
    'Forecast Watchdog',
    'Detects open deals that put the current period forecast at risk.',
    'Use HubSpot gold forecast and pipeline datasets. Rank risk by weighted amount, close date, probability and days without activity. Do not invent deals, owners or amounts.',
    'Executive, concise, evidence-first.',
    '["refinement__query_dataset","refinement__preview_transform","refinement__get_schema","mcp-infra__search_rag","mcp-infra__request_admin_help"]'::jsonb,
    '{"kinds":["document","schema"]}'::jsonb,
    'claude-sonnet-4-6',
    8192,
    0.3,
    '{"variables":{"days_without_activity":"14"},"schedule":{"cron":"0 9 * * MON","tz":"America/Mexico_City"}}'::jsonb
), (
    'hubspot',
    'stale_deal_chaser',
    'Stale Deal Chaser',
    'Lists open deals without recent activity so sellers can recover or close them.',
    'Use only gold_deals_estancados. If the user provides no threshold, use 14 days. Return seller summary first, then deal-level evidence.',
    'Direct, operational, no filler.',
    '["refinement__query_dataset","refinement__get_schema","mcp-infra__search_rag","mcp-infra__request_admin_help"]'::jsonb,
    '{"kinds":["document","schema"]}'::jsonb,
    'claude-haiku-4-5-20251001',
    4096,
    0.2,
    '{"variables":{"days_threshold":"14"},"schedule":{"cron":"0 9 * * MON","tz":"America/Mexico_City"}}'::jsonb
)
ON CONFLICT DO NOTHING;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('93_hubspot_role_and_seed.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
