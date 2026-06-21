#!/usr/bin/env python3
"""Seed and probe a large AWS mock Studio -> Gold -> Control Room flow."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from aws_ssm import (
    DEFAULT_REGION,
    REPO,
    redact,
    resolve_instance_id,
    send_ssm_script,
    utc_now,
    utc_stamp,
    write_json,
)


DEFAULT_EVIDENCE_ROOT = (
    REPO / "docs" / "release-evidence" / "control-room-mock-volume-aws"
)


@dataclass
class Check:
    name: str
    status: str
    evidence: str
    unblock: str = ""


def _safe_suffix(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in value.strip())
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    if not cleaned:
        cleaned = utc_stamp().lower().replace("z", "")
    if cleaned[0].isdigit():
        cleaned = f"r_{cleaned}"
    return cleaned[:40]


def _remote_probe_script(*, rows: int, suffix: str) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail
set +x

ROWS={int(rows)}
SUFFIX={json.dumps(suffix)}
REPO_DIR="${{REPO_DIR:-/opt/modecissions}}"
DEPLOY_DIR="${{DEPLOY_DIR:-${{REPO_DIR}}/infra/terraform/deploy}}"

emit() {{
  local name="$1"
  local status="$2"
  local evidence="${{3:-}}"
  local unblock="${{4:-}}"
  evidence="${{evidence//$'\\t'/ }}"; evidence="${{evidence//$'\\r'/ }}"; evidence="${{evidence//$'\\n'/ }}"
  unblock="${{unblock//$'\\t'/ }}"; unblock="${{unblock//$'\\r'/ }}"; unblock="${{unblock//$'\\n'/ }}"
  printf 'OMEGA_MOCK_VOLUME_CHECK\\t%s\\t%s\\t%s\\t%s\\n' "$name" "$status" "$evidence" "$unblock"
}}

env_value() {{
  local key="$1"
  if [ -f "${{DEPLOY_DIR}}/.env" ]; then
    awk -F= -v key="$key" '$1 == key {{print substr($0, index($0, "=") + 1)}}' "${{DEPLOY_DIR}}/.env" | tail -n 1 | sed "s/^[ '\\"]//; s/[ '\\"]$//"
  fi
}}

compose_files() {{
  printf -- '-f docker-compose.aws.yml '
  if [ "$(env_value DEPLOY_CARTRIDGES_SAME_HOST)" = "true" ] && [ -f docker-compose.cartridges.yml ]; then
    printf -- '-f docker-compose.cartridges.yml '
  fi
}}

psql_main() {{
  docker compose $(compose_files) exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -d modecissions -tAc "$1" 2>&1 | tr -d '\\r'
}}

psql_gold() {{
  docker compose $(compose_files) exec -T postgres_gold psql -v ON_ERROR_STOP=1 -U postgres -d modecissions_gold -p 5433 -tAc "$1" 2>&1 | tr -d '\\r'
}}

cd "$DEPLOY_DIR"

case "$SUFFIX" in
  *[!a-z0-9_]*|"")
    emit "safe suffix" "FAIL" "suffix=$SUFFIX" "Use lowercase letters, numbers and underscores."
    exit 0
    ;;
  *)
    emit "safe suffix" "PASS" "suffix=$SUFFIX rows=$ROWS"
    ;;
esac

writeback="$(env_value CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK | tr '[:upper:]' '[:lower:]')"
case "$writeback" in
  ""|"0"|"false"|"disabled")
    emit "external writeback disabled" "PASS" "CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK=${{writeback:-<unset>}}"
    ;;
  *)
    emit "external writeback disabled" "FAIL" "CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK=$writeback" "Disable external writeback before stress probing."
    ;;
esac

raw_dataset="mock_studio_events_${{SUFFIX}}"
margin_dataset="mock_project_margin_${{SUFFIX}}"
hours_dataset="mock_billable_hours_${{SUFFIX}}"
raw_table="gold_${{raw_dataset}}"
margin_table="gold_${{margin_dataset}}"
hours_table="gold_${{hours_dataset}}"
batch_id="mock-volume:${{SUFFIX}}"
tenant_name="Mock Volume Tenant ${{SUFFIX}}"
workspace_name="Mock Volume Workspace ${{SUFFIX}}"
tenant_slug="mock-volume-${{SUFFIX//_/-}}"
tenant_has_slug="$(psql_main "SELECT EXISTS (
  SELECT 1
    FROM information_schema.columns
   WHERE table_schema = 'public'
     AND table_name = 'tenants'
     AND column_name = 'slug'
);" || true)"
tenant_has_slug="$(echo "$tenant_has_slug" | sed '/^$/d' | tail -n 1)"
if [ "$tenant_has_slug" = "t" ] || [ "$tenant_has_slug" = "true" ]; then
  tenant_insert="INSERT INTO tenants(name, slug)
  VALUES ('${{tenant_name}}', '${{tenant_slug}}')
  ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
  RETURNING id"
  tenant_select="SELECT id FROM tenants WHERE slug = '${{tenant_slug}}'"
else
  tenant_insert="INSERT INTO tenants(name)
  VALUES ('${{tenant_name}}')
  ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
  RETURNING id"
  tenant_select="SELECT id FROM tenants WHERE name = '${{tenant_name}}'"
fi

scope="$(psql_main "WITH t AS (
  $tenant_insert
), selected_tenant AS (
  SELECT id FROM t
  UNION ALL
  $tenant_select
  LIMIT 1
), w AS (
  INSERT INTO workspaces(tenant_id, name)
  SELECT id, '${{workspace_name}}' FROM selected_tenant
  ON CONFLICT (tenant_id, name) DO UPDATE SET name = EXCLUDED.name
  RETURNING id, tenant_id
)
SELECT tenant_id::text || '|' || id::text FROM w LIMIT 1;" || true)"
scope="$(echo "$scope" | sed '/^$/d' | tail -n 1)"
if [ -z "$scope" ] || ! echo "$scope" | grep -q "|"; then
  emit "mock workspace scope" "FAIL" "${{scope:-<empty>}}" "Could not create isolated tenant/workspace."
  exit 0
fi
tenant_id="${{scope%%|*}}"
workspace_id="${{scope#*|}}"
emit "mock workspace scope" "PASS" "tenant=${{tenant_id}} workspace=${{workspace_id}}"

visibility_counts="$(psql_main "WITH scope AS (
  SELECT '${{tenant_id}}'::uuid AS tenant_id, '${{workspace_id}}'::uuid AS workspace_id
), product_pick AS (
  SELECT id AS product_id
    FROM marketplace_products
   WHERE cartridge_id = 'replicon'
     AND status IN ('active', 'internal')
   ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, id
   LIMIT 1
), role_pick AS (
  SELECT id AS role_id FROM roles WHERE name = 'workspace_admin' LIMIT 1
), admin_users AS (
  SELECT id AS user_id
    FROM users
   WHERE is_active IS TRUE
     AND role IN ('super_admin', 'admin', 'owner')
   ORDER BY id
), memberships AS (
  INSERT INTO user_workspace_roles (user_id, workspace_id, role_id)
  SELECT admin_users.user_id, scope.workspace_id, role_pick.role_id
    FROM scope, role_pick, admin_users
  ON CONFLICT DO NOTHING
  RETURNING 1
), entitlement AS (
  INSERT INTO tenant_entitlements (
    tenant_id, workspace_id, cartridge_id, product_id, status,
    activated_by_id, starts_at, created_at, updated_at
  )
  SELECT scope.tenant_id, scope.workspace_id, 'replicon', product_pick.product_id,
         'active', (SELECT user_id FROM admin_users LIMIT 1), NOW(), NOW(), NOW()
    FROM scope, product_pick
  ON CONFLICT (tenant_id, workspace_id, cartridge_id) DO UPDATE
    SET product_id = EXCLUDED.product_id,
        status = 'active',
        activated_by_id = EXCLUDED.activated_by_id,
        starts_at = COALESCE(tenant_entitlements.starts_at, NOW()),
        ends_at = NULL,
        updated_at = NOW()
  RETURNING 1
), installation AS (
  INSERT INTO cartridge_installations (
    id, tenant_id, workspace_id, cartridge_id, product_id,
    status, current_step, install_fingerprint, created_by_id,
    created_at, updated_at, ready_at
  )
  SELECT 'mockvol_' || md5(scope.tenant_id::text || ':' || scope.workspace_id::text || ':replicon'),
         scope.tenant_id, scope.workspace_id, 'replicon', product_pick.product_id,
         'ready', 'seeded_by_mock_volume_probe',
         md5(scope.tenant_id::text || ':' || scope.workspace_id::text || ':replicon'),
         (SELECT user_id FROM admin_users LIMIT 1), NOW(), NOW(), NOW()
    FROM scope, product_pick
  ON CONFLICT (workspace_id, cartridge_id) DO UPDATE
    SET product_id = EXCLUDED.product_id,
        status = 'ready',
        current_step = 'seeded_by_mock_volume_probe',
        error_message = NULL,
        ready_at = COALESCE(cartridge_installations.ready_at, NOW()),
        updated_at = NOW()
  RETURNING 1
)
SELECT
  (SELECT COUNT(*) FROM product_pick)::text
  || '|' ||
  (SELECT COUNT(*) FROM user_workspace_roles WHERE workspace_id='${{workspace_id}}'::uuid)::text
  || '|' ||
  (SELECT COUNT(*) FROM tenant_entitlements WHERE workspace_id='${{workspace_id}}'::uuid AND cartridge_id='replicon' AND status='active')::text
  || '|' ||
  (SELECT COUNT(*) FROM cartridge_installations WHERE workspace_id='${{workspace_id}}'::uuid AND cartridge_id='replicon' AND status='ready')::text
  || '|' ||
  (SELECT COALESCE(string_agg(te.cartridge_id, ',' ORDER BY te.cartridge_id), '')
     FROM tenant_entitlements te
    WHERE te.workspace_id='${{workspace_id}}'::uuid
      AND te.status='active'
      AND EXISTS (
        SELECT 1
          FROM cartridge_installations ci
         WHERE ci.tenant_id=te.tenant_id
           AND ci.workspace_id=te.workspace_id
           AND ci.cartridge_id=te.cartridge_id
           AND ci.status='ready'
      ));" || true)"
visibility_counts="$(echo "$visibility_counts" | sed '/^$/d' | tail -n 1)"
IFS='|' read -r product_count membership_count entitlement_count installation_count allowed_cartridges <<EOF_VISIBILITY
$visibility_counts
EOF_VISIBILITY
if [ "${{product_count:-0}}" -ge 1 ] 2>/dev/null && [ "${{membership_count:-0}}" -ge 1 ] 2>/dev/null && [ "${{entitlement_count:-0}}" -ge 1 ] 2>/dev/null && [ "${{installation_count:-0}}" -ge 1 ] 2>/dev/null && echo ",${{allowed_cartridges:-}}," | grep -q ",replicon,"; then
  emit "Mock workspace UI visibility" "PASS" "membership=${{membership_count}} entitlement=${{entitlement_count}} installation=${{installation_count}} allowed=${{allowed_cartridges}}"
else
  emit "Mock workspace UI visibility" "FAIL" "product=${{product_count:-unknown}} membership=${{membership_count:-unknown}} entitlement=${{entitlement_count:-unknown}} installation=${{installation_count:-unknown}} allowed=${{allowed_cartridges:-}}" "Expected replicon marketplace product, admin membership, active entitlement and ready installation."
fi

if ! gold_seed_output="$(
docker compose $(compose_files) exec -T postgres_gold psql -v ON_ERROR_STOP=1 -U postgres -d modecissions_gold -p 5433 2>&1 <<SQL
\\timing on
DROP TABLE IF EXISTS public."${{raw_table}}";
DROP TABLE IF EXISTS public."${{margin_table}}";
DROP TABLE IF EXISTS public."${{hours_table}}";

CREATE TABLE public."${{raw_table}}" (
  tenant_id UUID NOT NULL,
  workspace_id UUID NOT NULL,
  event_date DATE NOT NULL,
  event_id TEXT NOT NULL,
  source_system TEXT NOT NULL,
  region TEXT NOT NULL,
  customer_segment TEXT NOT NULL,
  project_code TEXT NOT NULL,
  consultant TEXT NOT NULL,
  account_manager TEXT NOT NULL,
  sku TEXT NOT NULL,
  quantity NUMERIC(18,4) NOT NULL,
  revenue_usd NUMERIC(18,4) NOT NULL,
  cost_usd NUMERIC(18,4) NOT NULL,
  billable_hours NUMERIC(18,4) NOT NULL,
  non_billable_hours NUMERIC(18,4) NOT NULL,
  risk_score NUMERIC(18,4) NOT NULL,
  quality_score NUMERIC(18,4) NOT NULL,
  cycle_time_days NUMERIC(18,4) NOT NULL,
  anomaly_flag BOOLEAN NOT NULL,
  variable_01 NUMERIC(18,4) NOT NULL,
  variable_02 NUMERIC(18,4) NOT NULL,
  variable_03 NUMERIC(18,4) NOT NULL,
  variable_04 NUMERIC(18,4) NOT NULL,
  variable_05 NUMERIC(18,4) NOT NULL,
  variable_06 NUMERIC(18,4) NOT NULL,
  variable_07 NUMERIC(18,4) NOT NULL,
  variable_08 NUMERIC(18,4) NOT NULL,
  variable_09 NUMERIC(18,4) NOT NULL,
  variable_10 NUMERIC(18,4) NOT NULL
);

INSERT INTO public."${{raw_table}}" (
  tenant_id, workspace_id, event_date, event_id, source_system, region,
  customer_segment, project_code, consultant, account_manager, sku, quantity,
  revenue_usd, cost_usd, billable_hours, non_billable_hours, risk_score,
  quality_score, cycle_time_days, anomaly_flag, variable_01, variable_02,
  variable_03, variable_04, variable_05, variable_06, variable_07,
  variable_08, variable_09, variable_10
)
SELECT
  '${{tenant_id}}'::uuid,
  '${{workspace_id}}'::uuid,
  (DATE '2025-01-01' + ((g % 540)::int))::date,
  'evt-' || '${{SUFFIX}}' || '-' || g::text,
  'studio_mock',
  'region-' || (g % 8)::text,
  CASE WHEN g % 5 = 0 THEN 'enterprise' WHEN g % 5 = 1 THEN 'midmarket' ELSE 'commercial' END,
  'MOCK-P' || lpad((g % 60)::text, 3, '0'),
  'consultant-' || lpad((g % 240)::text, 3, '0'),
  'manager-' || lpad((g % 24)::text, 2, '0'),
  'SKU-' || lpad((g % 180)::text, 3, '0'),
  ((g % 9) + 1)::numeric,
  round((120 + (g % 370) * 1.17 + (g % 17) * 3.9)::numeric, 4),
  round(((120 + (g % 370) * 1.17 + (g % 17) * 3.9)
      * (0.45 + ((g % 13)::numeric / 100.0)
         + CASE
             WHEN (DATE '2025-01-01' + ((g % 540)::int)) >= DATE '2026-06-01'
              AND (g % 60) IN (3, 7, 11, 19)
             THEN 0.48
             ELSE 0
           END))::numeric, 4),
  round((2.0 + (g % 11) * 0.42)::numeric, 4),
  round(((g % 5) * 0.31 + CASE
      WHEN (DATE '2025-01-01' + ((g % 540)::int)) >= DATE '2026-06-01'
       AND (g % 240) IN (5, 17, 29, 41, 53)
      THEN 4.5
      ELSE 0
    END)::numeric, 4),
  round(((g % 100)::numeric / 100.0 + CASE
      WHEN (DATE '2025-01-01' + ((g % 540)::int)) >= DATE '2026-06-01'
       AND (g % 60) IN (3, 7, 11, 19)
      THEN 6.0
      ELSE 0
    END)::numeric, 4),
  round((96 - (g % 19) * 0.7 - CASE WHEN (g % 97) = 0 THEN 16 ELSE 0 END)::numeric, 4),
  round((1 + (g % 21) * 0.25)::numeric, 4),
  ((DATE '2025-01-01' + ((g % 540)::int)) >= DATE '2026-06-01' AND (g % 60) IN (3, 7, 11, 19)),
  round(((g % 101) * 1.11)::numeric, 4),
  round(((g % 103) * 1.07)::numeric, 4),
  round(((g % 107) * 1.03)::numeric, 4),
  round(((g % 109) * 0.99)::numeric, 4),
  round(((g % 113) * 0.95)::numeric, 4),
  round(((g % 127) * 0.91)::numeric, 4),
  round(((g % 131) * 0.87)::numeric, 4),
  round(((g % 137) * 0.83)::numeric, 4),
  round(((g % 139) * 0.79)::numeric, 4),
  round(((g % 149) * 0.75)::numeric, 4)
FROM generate_series(1, $ROWS) AS s(g);

CREATE INDEX "${{raw_table}}_scope_idx" ON public."${{raw_table}}" (tenant_id, workspace_id);
CREATE INDEX "${{raw_table}}_project_date_idx" ON public."${{raw_table}}" (project_code, event_date);
ANALYZE public."${{raw_table}}";

CREATE TABLE public."${{margin_table}}" AS
SELECT
  tenant_id,
  workspace_id,
  date_trunc('month', event_date)::date AS month,
  project_code,
  region,
  account_manager,
  round(SUM(revenue_usd)::numeric, 4) AS revenue_usd,
  round(SUM(cost_usd)::numeric, 4) AS cost_usd,
  round((SUM(revenue_usd) - SUM(cost_usd))::numeric, 4) AS margin_usd,
  round((CASE WHEN SUM(revenue_usd) = 0 THEN 0 ELSE (SUM(revenue_usd) - SUM(cost_usd)) / SUM(revenue_usd) * 100 END)::numeric, 4) AS margin_pct,
  round(SUM(billable_hours)::numeric, 4) AS billable_hours,
  round(AVG(risk_score)::numeric, 4) AS risk_score,
  round((SUM(cost_usd) * AVG(risk_score))::numeric, 4) AS downside_risk_usd,
  COUNT(*)::bigint AS event_count
FROM public."${{raw_table}}"
GROUP BY tenant_id, workspace_id, date_trunc('month', event_date)::date, project_code, region, account_manager
ORDER BY project_code, month, region, account_manager;

CREATE TABLE public."${{hours_table}}" AS
SELECT
  tenant_id,
  workspace_id,
  date_trunc('month', event_date)::date AS month,
  consultant,
  account_manager,
  project_code,
  round(SUM(billable_hours)::numeric, 4) AS billable_hours,
  round(SUM(non_billable_hours)::numeric, 4) AS non_billable_hours,
  round((SUM(billable_hours) / NULLIF(SUM(billable_hours + non_billable_hours), 0) * 100)::numeric, 4) AS utilization_pct,
  round((SUM(non_billable_hours) * 180)::numeric, 4) AS cost_of_delay_usd,
  round(AVG(risk_score)::numeric, 4) AS risk_score,
  COUNT(*)::bigint AS event_count
FROM public."${{raw_table}}"
GROUP BY tenant_id, workspace_id, date_trunc('month', event_date)::date, consultant, account_manager, project_code
ORDER BY consultant, month, project_code;

CREATE INDEX "${{margin_table}}_scope_idx" ON public."${{margin_table}}" (tenant_id, workspace_id);
CREATE INDEX "${{margin_table}}_entity_idx" ON public."${{margin_table}}" (project_code, month);
CREATE INDEX "${{hours_table}}_scope_idx" ON public."${{hours_table}}" (tenant_id, workspace_id);
CREATE INDEX "${{hours_table}}_entity_idx" ON public."${{hours_table}}" (consultant, month);

SELECT public.omega_apply_gold_rls_for_table('${{raw_table}}');
SELECT public.omega_apply_gold_rls_for_table('${{margin_table}}');
SELECT public.omega_apply_gold_rls_for_table('${{hours_table}}');

DO \\$\\$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_refinement_gold') THEN
    EXECUTE 'GRANT SELECT ON public."${{raw_table}}" TO omega_refinement_gold';
    EXECUTE 'GRANT SELECT ON public."${{margin_table}}" TO omega_refinement_gold';
    EXECUTE 'GRANT SELECT ON public."${{hours_table}}" TO omega_refinement_gold';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_gold_reader') THEN
    EXECUTE 'GRANT SELECT ON public."${{raw_table}}" TO omega_gold_reader';
    EXECUTE 'GRANT SELECT ON public."${{margin_table}}" TO omega_gold_reader';
    EXECUTE 'GRANT SELECT ON public."${{hours_table}}" TO omega_gold_reader';
  END IF;
END
\\$\\$;
SQL
)"; then
  emit "Gold mock tables materialized" "FAIL" "$gold_seed_output" "Inspect Gold DDL/materialization SQL."
  exit 0
fi
emit "Gold mock tables materialized" "PASS" "$(echo "$gold_seed_output" | tail -n 8)"

raw_count="$(psql_gold "SELECT COUNT(*) FROM public.\\"${{raw_table}}\\" WHERE tenant_id::text='${{tenant_id}}' AND workspace_id::text='${{workspace_id}}';" | sed '/^$/d' | tail -n 1)"
margin_count="$(psql_gold "SELECT COUNT(*) FROM public.\\"${{margin_table}}\\" WHERE tenant_id::text='${{tenant_id}}' AND workspace_id::text='${{workspace_id}}';" | sed '/^$/d' | tail -n 1)"
hours_count="$(psql_gold "SELECT COUNT(*) FROM public.\\"${{hours_table}}\\" WHERE tenant_id::text='${{tenant_id}}' AND workspace_id::text='${{workspace_id}}';" | sed '/^$/d' | tail -n 1)"
if [ "${{raw_count:-0}}" -eq "$ROWS" ] 2>/dev/null && [ "${{margin_count:-0}}" -gt 0 ] 2>/dev/null && [ "${{hours_count:-0}}" -gt 0 ] 2>/dev/null; then
  emit "Gold row counts" "PASS" "raw=${{raw_count}} margin=${{margin_count}} hours=${{hours_count}}"
else
  emit "Gold row counts" "FAIL" "raw=${{raw_count:-unknown}} margin=${{margin_count:-unknown}} hours=${{hours_count:-unknown}}" "Expected raw rows to equal requested ROWS and aggregate rows > 0."
fi

if ! catalog_output="$(docker compose $(compose_files) exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -d modecissions 2>&1 <<SQL
INSERT INTO datasets (
  name, description, layer, cartridge, sources, sql_def, column_mapping,
  row_count, last_refresh, updated_at, workspace_id
)
VALUES
  ('mock_studio_raw_${{SUFFIX}}', 'Mock Studio uploaded source for AWS high-volume Control Room probe.', 'raw', 'replicon', '["studio/mock-upload"]'::jsonb, 'Synthetic source declared by Studio probe.', '{{"event_id":{{"type":"TEXT"}},"revenue_usd":{{"type":"NUMERIC"}}}}'::jsonb, $ROWS, NOW(), NOW(), '${{workspace_id}}'::uuid),
  ('mock_studio_silver_${{SUFFIX}}', 'Mock cleaned silver layer for AWS high-volume Control Room probe.', 'silver', 'replicon', '["mock_studio_raw_${{SUFFIX}}"]'::jsonb, 'Mock silver normalization of Studio source.', '{{"event_id":{{"type":"TEXT"}},"project_code":{{"type":"TEXT"}}}}'::jsonb, $ROWS, NOW(), NOW(), '${{workspace_id}}'::uuid),
  ('${{raw_dataset}}', 'Mock Gold detail table with one million Studio-like rows.', 'gold', 'replicon', '["mock_studio_silver_${{SUFFIX}}"]'::jsonb, 'Materialized in postgres_gold as ${{raw_table}}.', '{{"event_date":{{"type":"DATE"}},"project_code":{{"type":"TEXT"}},"revenue_usd":{{"type":"NUMERIC"}},"cost_usd":{{"type":"NUMERIC"}}}}'::jsonb, $ROWS, NOW(), NOW(), '${{workspace_id}}'::uuid),
  ('${{margin_dataset}}', 'Mock Gold aggregate for project margin and downside risk.', 'gold', 'replicon', '["${{raw_dataset}}"]'::jsonb, 'Aggregated from ${{raw_table}} for Control Room generic Gold fallback.', '{{"month":{{"type":"DATE"}},"project_code":{{"type":"TEXT"}},"downside_risk_usd":{{"type":"NUMERIC"}}}}'::jsonb, ${{margin_count:-0}}, NOW(), NOW(), '${{workspace_id}}'::uuid),
  ('${{hours_dataset}}', 'Mock Gold aggregate for billable hour leakage.', 'gold', 'replicon', '["${{raw_dataset}}"]'::jsonb, 'Aggregated from ${{raw_table}} for Control Room generic Gold fallback.', '{{"month":{{"type":"DATE"}},"consultant":{{"type":"TEXT"}},"cost_of_delay_usd":{{"type":"NUMERIC"}}}}'::jsonb, ${{hours_count:-0}}, NOW(), NOW(), '${{workspace_id}}'::uuid)
ON CONFLICT (name) DO UPDATE SET
  description = EXCLUDED.description,
  layer = EXCLUDED.layer,
  cartridge = EXCLUDED.cartridge,
  sources = EXCLUDED.sources,
  sql_def = EXCLUDED.sql_def,
  column_mapping = EXCLUDED.column_mapping,
  row_count = EXCLUDED.row_count,
  last_refresh = EXCLUDED.last_refresh,
  updated_at = NOW();

DELETE FROM silver_lineage WHERE cartridge_id = 'replicon' AND source_batch_id = '${{batch_id}}';
INSERT INTO silver_lineage (
  silver_name, cartridge_id, source_entity, source_load_date, source_batch_id,
  sql_def, column_mapping, layer, row_count, storage_uri, created_by, created_at
) VALUES
  ('mock_studio_raw_${{SUFFIX}}', 'replicon', 'studio/mock-upload', CURRENT_DATE, '${{batch_id}}', 'Mock Studio upload.', '{{}}'::jsonb, 'raw', $ROWS, 'studio://mock-volume/${{SUFFIX}}/raw', 'aws_mock_volume_probe', NOW()),
  ('mock_studio_silver_${{SUFFIX}}', 'replicon', 'mock_studio_raw_${{SUFFIX}}', CURRENT_DATE, '${{batch_id}}', 'Mock silver cleanup.', '{{}}'::jsonb, 'silver', $ROWS, 'postgres_gold://modecissions_gold/public/${{raw_table}}', 'aws_mock_volume_probe', NOW()),
  ('${{margin_dataset}}', 'replicon', '${{raw_dataset}}', CURRENT_DATE, '${{batch_id}}', 'Mock margin aggregation.', '{{}}'::jsonb, 'gold', ${{margin_count:-0}}, 'postgres_gold://modecissions_gold/public/${{margin_table}}', 'aws_mock_volume_probe', NOW()),
  ('${{hours_dataset}}', 'replicon', '${{raw_dataset}}', CURRENT_DATE, '${{batch_id}}', 'Mock billable-hours aggregation.', '{{}}'::jsonb, 'gold', ${{hours_count:-0}}, 'postgres_gold://modecissions_gold/public/${{hours_table}}', 'aws_mock_volume_probe', NOW());

INSERT INTO studio_entities (name, cartridge, spec, created_by, updated_at)
VALUES (
  'mock_volume_${{SUFFIX}}',
  'replicon',
  jsonb_build_object(
    'source', 'studio_mock_upload',
    'suffix', '${{SUFFIX}}',
    'tenant_id', '${{tenant_id}}',
    'workspace_id', '${{workspace_id}}',
    'rows', $ROWS,
    'layers', jsonb_build_array('raw', 'silver', 'gold'),
    'datasets', jsonb_build_array('${{raw_dataset}}', '${{margin_dataset}}', '${{hours_dataset}}'),
    'cleanup_hint', 'make control-room-mock-volume-aws-cleanup OMEGA_MOCK_VOLUME_SUFFIX=${{SUFFIX}}'
  ),
  NULL,
  NOW()
)
ON CONFLICT (name, cartridge) DO UPDATE SET spec = EXCLUDED.spec, updated_at = NOW();

DELETE FROM data_catalog WHERE dataset IN ('${{raw_dataset}}', '${{margin_dataset}}', '${{hours_dataset}}');
INSERT INTO data_catalog (dataset, layer, cartridge, column_name, data_type, description, example_values, tags, is_key, is_metric, created_at, updated_at)
VALUES
  ('${{raw_dataset}}', 'gold', 'replicon', 'event_date', 'DATE', 'Mock Studio event date.', '[]'::jsonb, ARRAY['mock_volume','date'], false, false, NOW(), NOW()),
  ('${{raw_dataset}}', 'gold', 'replicon', 'project_code', 'TEXT', 'Mock project code.', '[]'::jsonb, ARRAY['mock_volume','key'], true, false, NOW(), NOW()),
  ('${{raw_dataset}}', 'gold', 'replicon', 'revenue_usd', 'NUMERIC', 'Mock event revenue.', '[]'::jsonb, ARRAY['mock_volume','metric'], false, true, NOW(), NOW()),
  ('${{raw_dataset}}', 'gold', 'replicon', 'cost_usd', 'NUMERIC', 'Mock event cost.', '[]'::jsonb, ARRAY['mock_volume','metric'], false, true, NOW(), NOW()),
  ('${{margin_dataset}}', 'gold', 'replicon', 'month', 'DATE', 'Aggregation month.', '[]'::jsonb, ARRAY['mock_volume','date'], false, false, NOW(), NOW()),
  ('${{margin_dataset}}', 'gold', 'replicon', 'project_code', 'TEXT', 'Project entity.', '[]'::jsonb, ARRAY['mock_volume','key'], true, false, NOW(), NOW()),
  ('${{margin_dataset}}', 'gold', 'replicon', 'downside_risk_usd', 'NUMERIC', 'Deterministic downside risk metric.', '[]'::jsonb, ARRAY['mock_volume','metric','risk'], false, true, NOW(), NOW()),
  ('${{hours_dataset}}', 'gold', 'replicon', 'month', 'DATE', 'Aggregation month.', '[]'::jsonb, ARRAY['mock_volume','date'], false, false, NOW(), NOW()),
  ('${{hours_dataset}}', 'gold', 'replicon', 'consultant', 'TEXT', 'Consultant entity.', '[]'::jsonb, ARRAY['mock_volume','key','person'], true, false, NOW(), NOW()),
  ('${{hours_dataset}}', 'gold', 'replicon', 'cost_of_delay_usd', 'NUMERIC', 'Deterministic cost-of-delay metric.', '[]'::jsonb, ARRAY['mock_volume','metric','risk'], false, true, NOW(), NOW())
ON CONFLICT (dataset, column_name) DO UPDATE SET
  layer = EXCLUDED.layer,
  cartridge = EXCLUDED.cartridge,
  data_type = EXCLUDED.data_type,
  description = EXCLUDED.description,
  example_values = EXCLUDED.example_values,
  tags = EXCLUDED.tags,
  is_key = EXCLUDED.is_key,
  is_metric = EXCLUDED.is_metric,
  updated_at = NOW();
SQL
)"; then
  emit "Studio and layer metadata" "FAIL" "$catalog_output" "Inspect operational metadata inserts."
  exit 0
fi
emit "Studio and layer metadata" "PASS" "$(echo "$catalog_output" | tail -n 8)"

metadata_counts="$(psql_main "SELECT
  (SELECT COUNT(*) FROM datasets WHERE name IN ('mock_studio_raw_${{SUFFIX}}','mock_studio_silver_${{SUFFIX}}','${{raw_dataset}}','${{margin_dataset}}','${{hours_dataset}}'))::text
  || '|' ||
  (SELECT COUNT(*) FROM silver_lineage WHERE source_batch_id='${{batch_id}}')::text
  || '|' ||
  (SELECT COUNT(*) FROM studio_entities WHERE name='mock_volume_${{SUFFIX}}' AND cartridge='replicon')::text
  || '|' ||
  (SELECT COUNT(*) FROM data_catalog WHERE dataset IN ('${{raw_dataset}}','${{margin_dataset}}','${{hours_dataset}}'))::text;")"
metadata_counts="$(echo "$metadata_counts" | sed '/^$/d' | tail -n 1)"
IFS='|' read -r dataset_meta lineage_meta studio_meta catalog_meta <<EOF_COUNTS
$metadata_counts
EOF_COUNTS
if [ "${{dataset_meta:-0}}" -ge 5 ] 2>/dev/null && [ "${{lineage_meta:-0}}" -ge 4 ] 2>/dev/null && [ "${{studio_meta:-0}}" -eq 1 ] 2>/dev/null && [ "${{catalog_meta:-0}}" -ge 10 ] 2>/dev/null; then
  emit "Studio layer metadata visible" "PASS" "datasets=${{dataset_meta}} lineage=${{lineage_meta}} studio_entities=${{studio_meta}} catalog=${{catalog_meta}}"
else
  emit "Studio layer metadata visible" "FAIL" "datasets=${{dataset_meta:-unknown}} lineage=${{lineage_meta:-unknown}} studio_entities=${{studio_meta:-unknown}} catalog=${{catalog_meta:-unknown}}" "Expected dataset, lineage, Studio entity and catalog metadata."
fi

stamp="$(date -u +%Y%m%dT%H%M%SZ)-$$"
dag_run_id="mock-volume-gold-engine-${{SUFFIX}}-${{stamp}}"
run_ref="gold-refresh:${{workspace_id}}:replicon:${{dag_run_id}}"

run_endpoint() {{
  TENANT_ID="$tenant_id" WORKSPACE_ID="$workspace_id" DAG_RUN_ID="$dag_run_id" \
  MARGIN_DATASET="$margin_dataset" HOURS_DATASET="$hours_dataset" ROWS="$ROWS" SUFFIX="$SUFFIX" \
  docker compose $(compose_files) exec -T \
    -e TENANT_ID -e WORKSPACE_ID -e DAG_RUN_ID -e MARGIN_DATASET -e HOURS_DATASET -e ROWS -e SUFFIX \
    console python - <<'PY' 2>&1 || true
import asyncio
import json
import os
from app.routers import intelligence as intelligence_router

async def main() -> None:
    body = intelligence_router.GoldRefreshIntelligenceRequest(
        tenant_id=os.environ["TENANT_ID"],
        workspace_id=os.environ["WORKSPACE_ID"],
        cartridge_id="replicon",
        airflow_dag_run_id=os.environ["DAG_RUN_ID"],
        pipeline_run_id="mock-volume:" + os.environ["SUFFIX"] + ":" + os.environ["ROWS"],
        materialization_status="success",
        datasets=[os.environ["MARGIN_DATASET"], os.environ["HOURS_DATASET"]],
        finished_at="2026-06-21T00:00:00+00:00",
    )
    result = await intelligence_router.intelligence_gold_refresh_internal(
        body,
        internal_service="airflow",
    )
    print("OMEGA_MOCK_VOLUME_ENDPOINT_JSON=" + json.dumps(result, sort_keys=True))

asyncio.run(main())
PY
}}

first_probe="$(run_endpoint)"
first_json="$(echo "$first_probe" | grep 'OMEGA_MOCK_VOLUME_ENDPOINT_JSON=' | tail -n 1 | cut -d= -f2-)"
if echo "$first_json" | grep -q '"ok": true' && echo "$first_json" | grep -q '"signals": [1-9]'; then
  emit "Gold refresh first run" "PASS" "$first_json"
else
  emit "Gold refresh first run" "FAIL" "${{first_probe:-<empty>}}" "Control Room should generate generic Gold signals from aggregate mock datasets."
fi

events_first="$(psql_main "SELECT COUNT(*) FROM control_room_item_events WHERE workspace_id::text='${{workspace_id}}' AND metadata->>'run_ref'='${{run_ref}}';" | sed '/^$/d' | tail -n 1)"

second_probe="$(run_endpoint)"
second_json="$(echo "$second_probe" | grep 'OMEGA_MOCK_VOLUME_ENDPOINT_JSON=' | tail -n 1 | cut -d= -f2-)"
if echo "$second_json" | grep -q '"ok": true' && echo "$second_json" | grep -q '"idempotent": true' && echo "$second_json" | grep -q '"signals": 0'; then
  emit "Gold refresh retry idempotent" "PASS" "$second_json"
else
  emit "Gold refresh retry idempotent" "FAIL" "${{second_probe:-<empty>}}" "Retry must not create duplicate signals."
fi

events_second="$(psql_main "SELECT COUNT(*) FROM control_room_item_events WHERE workspace_id::text='${{workspace_id}}' AND metadata->>'run_ref'='${{run_ref}}';" | sed '/^$/d' | tail -n 1)"
if [ "${{events_first:-0}}" = "${{events_second:-0}}" ] && [ "${{events_second:-0}}" -gt 0 ] 2>/dev/null; then
  emit "Control Room retry event idempotency" "PASS" "events=${{events_second}}"
else
  emit "Control Room retry event idempotency" "FAIL" "first=${{events_first:-unknown}} second=${{events_second:-unknown}}" "Control Room events duplicated or missing."
fi

item_summary="$(psql_main "SELECT
  COUNT(*)::text || '|' ||
  COALESCE(MAX((metadata->'priority'->>'score')::numeric)::text, '0') || '|' ||
  COUNT(*) FILTER (
    WHERE metadata->>'control_origin' = 'generic_gold_signal'
      AND metadata ? 'math_provenance'
      AND metadata->'math_provenance'->>'ruleset_version' = 'control_room_gold_signal.v1'
      AND metadata ? 'priority'
      AND jsonb_typeof(metadata->'priority'->'drivers') = 'object'
      AND metadata ? 'monte_carlo'
      AND COALESCE(metadata->'monte_carlo'->>'status', '') <> ''
      AND metadata ? 'bayesian_calibration'
      AND metadata->'bayesian_calibration'->>'status' IN ('calibrated', 'not_calibrated')
      AND metadata ? 'evidence_pack'
  )::text
FROM control_room_items
WHERE workspace_id::text='${{workspace_id}}'
  AND metadata->>'run_ref'='${{run_ref}}'
  AND source_dataset IN ('${{margin_dataset}}','${{hours_dataset}}');")"
item_summary="$(echo "$item_summary" | sed '/^$/d' | tail -n 1)"
IFS='|' read -r item_count max_priority valid_items <<EOF_ITEMS
$item_summary
EOF_ITEMS
if [ "${{item_count:-0}}" -gt 0 ] 2>/dev/null && [ "${{valid_items:-0}}" -eq "${{item_count:-0}}" ] 2>/dev/null; then
  emit "Control Room metadata for mock volume" "PASS" "items=${{item_count}} valid=${{valid_items}} max_priority=${{max_priority}} run_ref=${{run_ref}}"
else
  emit "Control Room metadata for mock volume" "FAIL" "items=${{item_count:-unknown}} valid=${{valid_items:-unknown}} max_priority=${{max_priority:-unknown}} run_ref=${{run_ref}}" "Expected all mock Control Room items to include provenance, priority, Monte Carlo, Bayes and evidence."
fi

sample_item="$(psql_main "SELECT item_id || ' / ' || title || ' / ' || severity || ' / ' || COALESCE(metadata->'priority'->>'score','')
FROM control_room_items
WHERE workspace_id::text='${{workspace_id}}'
  AND metadata->>'run_ref'='${{run_ref}}'
ORDER BY (metadata->'priority'->>'score')::numeric DESC NULLS LAST
LIMIT 1;")"
sample_item="$(echo "$sample_item" | sed '/^$/d' | tail -n 1)"
emit "Sample Control Room item" "PASS" "${{sample_item:-<none>}}"

emit "Cleanup command" "PASS" "make control-room-mock-volume-aws-cleanup OMEGA_MOCK_VOLUME_SUFFIX=${{SUFFIX}}"
"""


def _remote_cleanup_script(*, suffix: str) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail
set +x

SUFFIX={json.dumps(suffix)}
REPO_DIR="${{REPO_DIR:-/opt/modecissions}}"
DEPLOY_DIR="${{DEPLOY_DIR:-${{REPO_DIR}}/infra/terraform/deploy}}"

emit() {{
  local name="$1"
  local status="$2"
  local evidence="${{3:-}}"
  local unblock="${{4:-}}"
  evidence="${{evidence//$'\\t'/ }}"; evidence="${{evidence//$'\\r'/ }}"; evidence="${{evidence//$'\\n'/ }}"
  unblock="${{unblock//$'\\t'/ }}"; unblock="${{unblock//$'\\r'/ }}"; unblock="${{unblock//$'\\n'/ }}"
  printf 'OMEGA_MOCK_VOLUME_CHECK\\t%s\\t%s\\t%s\\t%s\\n' "$name" "$status" "$evidence" "$unblock"
}}

env_value() {{
  local key="$1"
  if [ -f "${{DEPLOY_DIR}}/.env" ]; then
    awk -F= -v key="$key" '$1 == key {{print substr($0, index($0, "=") + 1)}}' "${{DEPLOY_DIR}}/.env" | tail -n 1 | sed "s/^[ '\\"]//; s/[ '\\"]$//"
  fi
}}

compose_files() {{
  printf -- '-f docker-compose.aws.yml '
  if [ "$(env_value DEPLOY_CARTRIDGES_SAME_HOST)" = "true" ] && [ -f docker-compose.cartridges.yml ]; then
    printf -- '-f docker-compose.cartridges.yml '
  fi
}}

psql_main() {{
  docker compose $(compose_files) exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -d modecissions -tAc "$1" 2>&1 | tr -d '\\r'
}}

cd "$DEPLOY_DIR"

case "$SUFFIX" in
  *[!a-z0-9_]*|"")
    emit "safe suffix" "FAIL" "suffix=$SUFFIX" "Use the exact suffix emitted by the probe."
    exit 0
    ;;
esac

raw_dataset="mock_studio_events_${{SUFFIX}}"
margin_dataset="mock_project_margin_${{SUFFIX}}"
hours_dataset="mock_billable_hours_${{SUFFIX}}"
raw_table="gold_${{raw_dataset}}"
margin_table="gold_${{margin_dataset}}"
hours_table="gold_${{hours_dataset}}"
batch_id="mock-volume:${{SUFFIX}}"
tenant_name="Mock Volume Tenant ${{SUFFIX}}"

docker compose $(compose_files) exec -T postgres_gold psql -v ON_ERROR_STOP=1 -U postgres -d modecissions_gold -p 5433 <<SQL
DROP TABLE IF EXISTS public."${{raw_table}}";
DROP TABLE IF EXISTS public."${{margin_table}}";
DROP TABLE IF EXISTS public."${{hours_table}}";
SQL

main_cleanup="$(docker compose $(compose_files) exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -d modecissions <<SQL
DELETE FROM datasets WHERE name IN ('mock_studio_raw_${{SUFFIX}}','mock_studio_silver_${{SUFFIX}}','${{raw_dataset}}','${{margin_dataset}}','${{hours_dataset}}');
DELETE FROM silver_lineage WHERE source_batch_id = '${{batch_id}}';
DELETE FROM data_catalog WHERE dataset IN ('${{raw_dataset}}','${{margin_dataset}}','${{hours_dataset}}');
DELETE FROM studio_entities WHERE name = 'mock_volume_${{SUFFIX}}' AND cartridge = 'replicon';
WITH target_workspaces AS (
  SELECT w.id
    FROM workspaces w
    JOIN tenants t ON t.id = w.tenant_id
   WHERE t.name = '${{tenant_name}}'
)
DELETE FROM decision_intelligence_snapshots WHERE workspace_id IN (SELECT id FROM target_workspaces);
WITH target_workspaces AS (
  SELECT w.id
    FROM workspaces w
    JOIN tenants t ON t.id = w.tenant_id
   WHERE t.name = '${{tenant_name}}'
)
DELETE FROM prediction_outcomes WHERE workspace_id IN (SELECT id FROM target_workspaces);
WITH target_workspaces AS (
  SELECT w.id
    FROM workspaces w
    JOIN tenants t ON t.id = w.tenant_id
   WHERE t.name = '${{tenant_name}}'
)
DELETE FROM decision_options WHERE workspace_id IN (SELECT id FROM target_workspaces);
WITH target_workspaces AS (
  SELECT w.id
    FROM workspaces w
    JOIN tenants t ON t.id = w.tenant_id
   WHERE t.name = '${{tenant_name}}'
)
DELETE FROM hypotheses WHERE workspace_id IN (SELECT id FROM target_workspaces);
WITH target_workspaces AS (
  SELECT w.id
    FROM workspaces w
    JOIN tenants t ON t.id = w.tenant_id
   WHERE t.name = '${{tenant_name}}'
)
DELETE FROM evidence_items WHERE workspace_id IN (SELECT id FROM target_workspaces);
WITH target_workspaces AS (
  SELECT w.id
    FROM workspaces w
    JOIN tenants t ON t.id = w.tenant_id
   WHERE t.name = '${{tenant_name}}'
)
DELETE FROM evidence_packs WHERE workspace_id IN (SELECT id FROM target_workspaces);
WITH target_workspaces AS (
  SELECT w.id
    FROM workspaces w
    JOIN tenants t ON t.id = w.tenant_id
   WHERE t.name = '${{tenant_name}}'
)
DELETE FROM control_room_item_events WHERE workspace_id IN (SELECT id FROM target_workspaces);
WITH target_workspaces AS (
  SELECT w.id
    FROM workspaces w
    JOIN tenants t ON t.id = w.tenant_id
   WHERE t.name = '${{tenant_name}}'
)
DELETE FROM control_room_items WHERE workspace_id IN (SELECT id FROM target_workspaces);
WITH target_workspaces AS (
  SELECT w.id
    FROM workspaces w
    JOIN tenants t ON t.id = w.tenant_id
   WHERE t.name = '${{tenant_name}}'
)
DELETE FROM intelligence_signals WHERE workspace_id IN (SELECT id FROM target_workspaces);
WITH target_workspaces AS (
  SELECT w.id
    FROM workspaces w
    JOIN tenants t ON t.id = w.tenant_id
   WHERE t.name = '${{tenant_name}}'
)
DELETE FROM metric_baselines WHERE workspace_id IN (SELECT id FROM target_workspaces);
WITH target_workspaces AS (
  SELECT w.id
    FROM workspaces w
    JOIN tenants t ON t.id = w.tenant_id
   WHERE t.name = '${{tenant_name}}'
)
DELETE FROM intelligence_runs WHERE workspace_id IN (SELECT id FROM target_workspaces);
DELETE FROM workspaces
 WHERE tenant_id IN (SELECT id FROM tenants WHERE name = '${{tenant_name}}')
   AND name = 'Mock Volume Workspace ${{SUFFIX}}';
DELETE FROM tenants WHERE name = '${{tenant_name}}';
SQL
)"
remaining="$(psql_main "SELECT
  (SELECT COUNT(*) FROM datasets WHERE name IN ('mock_studio_raw_${{SUFFIX}}','mock_studio_silver_${{SUFFIX}}','${{raw_dataset}}','${{margin_dataset}}','${{hours_dataset}}'))::text
  || '|' ||
  (SELECT COUNT(*) FROM silver_lineage WHERE source_batch_id='${{batch_id}}')::text
  || '|' ||
  (SELECT COUNT(*) FROM studio_entities WHERE name='mock_volume_${{SUFFIX}}' AND cartridge='replicon')::text
  || '|' ||
  (SELECT COUNT(*) FROM tenants WHERE name='${{tenant_name}}')::text;")"
remaining="$(echo "$remaining" | sed '/^$/d' | tail -n 1)"
emit "cleanup completed" "PASS" "$remaining $main_cleanup"
"""


def _parse(stdout: str) -> list[Check]:
    checks: list[Check] = []
    for line in stdout.splitlines():
        if not line.startswith("OMEGA_MOCK_VOLUME_CHECK\t"):
            continue
        _prefix, name, status, evidence, unblock = (line.split("\t", 4) + [""])[:5]
        checks.append(
            Check(
                name=name,
                status=status,
                evidence=redact(evidence),
                unblock=redact(unblock),
            )
        )
    return checks


def _overall_status(checks: list[Check]) -> str:
    if any(check.status == "FAIL" for check in checks):
        return "FAIL"
    if any(check.status == "BLOCKED" for check in checks):
        return "BLOCKED"
    return "PASS"


def _write_report(evidence_dir: Path, summary: dict) -> None:
    title = (
        "AWS Control Room Mock Volume Cleanup"
        if summary.get("mode") == "cleanup"
        else "AWS Control Room Mock Volume Probe"
    )
    lines = [
        f"# {title}",
        "",
        f"- status: `{summary['status']}`",
        f"- mode: `{summary['mode']}`",
        f"- generated_at_utc: `{summary['generated_at_utc']}`",
        f"- suffix: `{summary['suffix']}`",
        f"- rows: `{summary.get('rows')}`",
        f"- ssm_command_id: `{summary.get('ssm_command_id')}`",
        "",
        "| Check | Status | Evidence | Unblock |",
        "|---|---|---|---|",
    ]
    for check in summary["checks"]:
        evidence = str(check["evidence"]).replace("|", "\\|")
        unblock = str(check.get("unblock") or "").replace("|", "\\|")
        lines.append(
            f"| {check['name']} | {check['status']} | {evidence} | {unblock} |"
        )
    (evidence_dir / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed/probe large AWS mock Gold data through Control Room."
    )
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument(
        "--instance-id", default=os.environ.get("AWS_APP_INSTANCE_ID") or ""
    )
    parser.add_argument(
        "--rows",
        type=int,
        default=int(os.environ.get("OMEGA_MOCK_VOLUME_ROWS", "1000000")),
    )
    parser.add_argument(
        "--suffix",
        default=os.environ.get("OMEGA_MOCK_VOLUME_SUFFIX") or "",
        help="Optional deterministic suffix for probe dataset names.",
    )
    parser.add_argument(
        "--cleanup-suffix",
        default=os.environ.get("OMEGA_MOCK_VOLUME_CLEANUP_SUFFIX") or "",
        help="Delete a previous mock volume run by suffix instead of probing.",
    )
    parser.add_argument("--evidence-dir", type=Path, default=None)
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=int(os.environ.get("OMEGA_AWS_MOCK_VOLUME_TIMEOUT_SECONDS", "1800")),
    )
    args = parser.parse_args(argv)

    if args.rows < 1000:
        raise SystemExit("--rows must be >= 1000")
    if args.rows > 5_000_000:
        raise SystemExit("--rows must be <= 5000000")

    mode = "cleanup" if args.cleanup_suffix else "probe"
    suffix = _safe_suffix(args.cleanup_suffix or args.suffix or utc_stamp())
    evidence_dir = args.evidence_dir or DEFAULT_EVIDENCE_ROOT / utc_stamp()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    instance_id = resolve_instance_id(args.region, args.instance_id or None)
    script = (
        _remote_cleanup_script(suffix=suffix)
        if mode == "cleanup"
        else _remote_probe_script(rows=args.rows, suffix=suffix)
    )
    remote = send_ssm_script(
        region=args.region,
        instance_id=instance_id,
        script=script,
        comment=f"omega-control-room-mock-volume-{mode}",
        timeout_seconds=args.timeout_seconds,
    )
    checks = _parse(remote.stdout)
    checks.append(
        Check(
            name="SSM command completed",
            status="PASS" if remote.status == "Success" else "FAIL",
            evidence=(
                f"command_id={remote.command_id} status={remote.status} "
                f"response_code={remote.response_code}"
            ),
        )
    )
    status = _overall_status(checks)
    summary = {
        "status": status,
        "mode": mode,
        "generated_at_utc": utc_now().isoformat(),
        "suffix": suffix,
        "rows": args.rows,
        "ssm_command_id": remote.command_id,
        "instance_id": instance_id,
        "region": args.region,
        "ssm_status": remote.status,
        "ssm_response_code": remote.response_code,
        "checks": [asdict(check) for check in checks],
    }
    write_json(evidence_dir / "summary.json", summary)
    (evidence_dir / "remote_stdout_redacted.txt").write_text(
        redact(remote.stdout), encoding="utf-8"
    )
    (evidence_dir / "remote_stderr_redacted.txt").write_text(
        redact(remote.stderr), encoding="utf-8"
    )
    _write_report(evidence_dir, summary)
    print(
        json.dumps(
            {
                "status": status,
                "mode": mode,
                "suffix": suffix,
                "evidence_dir": str(evidence_dir),
                "ssm_command_id": remote.command_id,
            },
            indent=2,
        )
    )
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
