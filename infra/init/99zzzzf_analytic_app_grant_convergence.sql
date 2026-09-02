-- 99zzzzf_analytic_app_grant_convergence.sql
--
-- Installations that were already ready before the durable app-grant ledger
-- was introduced never crossed the activation transition that calls the
-- reconciler.  Backfill the reviewed manifests that already have a packaged
-- analytic_apps parent row; the startup reconciler handles apps inserted by
-- the runtime seed after migrations finish.
--
-- This is intentionally a new forward migration. Some existing environments
-- received an emergency 99zzzze backfill before it reached main; reusing that
-- historical filename with different bytes would invalidate migration drift
-- detection.

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
    JOIN public.analytic_apps a
      ON a.name = m.app_name
     AND a.cartridge_id = m.cartridge_id
     AND a.created_by_id IS NULL
     AND a.scope_status = 'platform_template'
     AND a.tenant_id IS NULL
     AND a.workspace_id IS NULL
    JOIN public.analytic_app_manifest_datasets d
      ON d.app_name = m.app_name
     AND d.manifest_digest = m.manifest_digest
    JOIN public.datasets ds
      ON ds.tenant_id = ci.tenant_id
     AND ds.workspace_id = ci.workspace_id
     AND ds.name = d.dataset_name
     AND ds.cartridge = m.cartridge_id
     AND ds.scope_status = 'scoped'
   WHERE ci.status = 'ready'
     AND NOT EXISTS (
           SELECT 1 FROM public.analytic_app_dataset_grants g
            WHERE g.tenant_id = ci.tenant_id
              AND g.workspace_id = ci.workspace_id
              AND g.app_name = m.app_name
              AND g.dataset_name = d.dataset_name
              AND g.manifest_digest = m.manifest_digest
              AND g.revoked_at IS NULL)
  ON CONFLICT DO NOTHING
  RETURNING id, tenant_id, workspace_id, manifest_digest
)
INSERT INTO public.analytic_app_dataset_grant_events
    (grant_id, tenant_id, workspace_id, event, actor, detail)
SELECT id, tenant_id, workspace_id, 'granted',
       'server:packaged_manifest', manifest_digest
  FROM ins;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zzzzf_analytic_app_grant_convergence.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
