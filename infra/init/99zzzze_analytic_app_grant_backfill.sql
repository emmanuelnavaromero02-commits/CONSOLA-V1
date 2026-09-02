-- 99zzzze_analytic_app_grant_backfill.sql
--
-- Fix: packaged analytic apps open with "0 datasets autorizados para esta app"
-- (and the embedded viewer refuses to connect) even though the app list shows
-- "Datos completos". The authority is analytic_app_dataset_grants (99zzt); it is
-- populated only when an installation transitions to 'ready'
-- (reconcile_analytic_app_dataset_grants, called from _set_installation_state).
--
-- Installations that were already 'ready' BEFORE the grant ledger shipped never
-- re-triggered the reconcile, so their grants stayed empty and every packaged
-- app resolved zero authorized datasets. This surfaced the first time the ledger
-- reached an environment with pre-existing ready installations (e.g. the GCP
-- staging deploy of v1.45.224-beta).
--
-- Forward-only, idempotent backfill: reproduce EXACTLY what the reconcile grants
-- -- packaged & active manifests x ready installations x MATERIALISED gold
-- datasets -- for every scope still missing them. It never revokes, and it only
-- grants datasets that actually exist in public.datasets, mirroring
-- reconcile_analytic_app_dataset_grants (a partially-materialised cartridge is an
-- absent grant, not an error). A user-created app that shares a packaged name is
-- excluded, exactly as the function does.

WITH ins AS (
  INSERT INTO public.analytic_app_dataset_grants
    (tenant_id, workspace_id, app_name, cartridge_id, dataset_name,
     manifest_digest, grant_source, granted_by)
  SELECT DISTINCT
         ci.tenant_id, ci.workspace_id, m.app_name, m.cartridge_id,
         d.dataset_name, m.manifest_digest, 'packaged_manifest',
         'server:packaged_manifest'
    FROM public.cartridge_installations ci
    JOIN public.analytic_app_manifests m
      ON m.cartridge_id = ci.cartridge_id
     AND m.revision = 'active'
     AND m.source = 'packaged_manifest'
    JOIN public.analytic_app_manifest_datasets d
      ON d.app_name = m.app_name
     AND d.manifest_digest = m.manifest_digest
    JOIN public.datasets ds
      ON ds.tenant_id = ci.tenant_id
     AND ds.workspace_id = ci.workspace_id
     AND ds.name = d.dataset_name
     AND ds.cartridge = m.cartridge_id
   WHERE ci.status = 'ready'
     AND NOT EXISTS (
           SELECT 1 FROM public.analytic_apps a
            WHERE a.name = m.app_name AND a.created_by_id IS NOT NULL)
     AND NOT EXISTS (
           SELECT 1 FROM public.analytic_app_dataset_grants g
            WHERE g.tenant_id = ci.tenant_id
              AND g.workspace_id = ci.workspace_id
              AND g.app_name = m.app_name
              AND g.dataset_name = d.dataset_name
              AND g.manifest_digest = m.manifest_digest
              AND g.revoked_at IS NULL)
  RETURNING id, tenant_id, workspace_id, manifest_digest
)
INSERT INTO public.analytic_app_dataset_grant_events
    (grant_id, tenant_id, workspace_id, event, actor, detail)
SELECT id, tenant_id, workspace_id, 'granted',
       'server:packaged_manifest', manifest_digest
  FROM ins;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zzzze_analytic_app_grant_backfill.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
