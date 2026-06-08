-- Make SAP SuccessFactors packaged apps resilient to optional Gold datasets.
-- Existing environments already have 87_sap_successfactors_apps_seed.sql marked
-- as applied, so this follow-up migration updates the stored HTML in place.

CREATE TABLE IF NOT EXISTS schema_migrations (
    id BIGSERIAL PRIMARY KEY,
    filename TEXT NOT NULL UNIQUE,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checksum TEXT
);

UPDATE analytic_apps
   SET html = replace(
       replace(
         html,
         $needle$async function fetchJson(url){
  const r = await fetch(url);
  if(!r.ok) throw new Error(url + ' (' + r.status + ')');
  const d = await r.json();
  return Array.isArray(d) ? d : (d.data || []);
}
$needle$,
         $replacement$async function fetchJson(url){
  const r = await fetch(url);
  if(!r.ok) throw new Error(url + ' (' + r.status + ')');
  const d = await r.json();
  return Array.isArray(d) ? d : (d.data || []);
}
async function fetchOptional(url){
  try { return await fetchJson(url); }
  catch(e) { console.warn('dataset opcional no disponible', url, e.message); return []; }
}
$replacement$
       ),
       $needle$fetchJson('/api/data/sap_successfactors_turnover_by_period')$needle$,
       $replacement$fetchOptional('/api/data/sap_successfactors_turnover_by_period')$replacement$
     ),
       updated_at = NOW()
 WHERE name = 'sap_successfactors_workforce_overview'
   AND html NOT LIKE '%fetchOptional%';

UPDATE analytic_apps
   SET html = replace(
       replace(
         replace(
           html,
           $needle$async function fetchJson(url){
  const r = await fetch(url);
  if(!r.ok) throw new Error(url + ' (' + r.status + ')');
  const d = await r.json();
  return Array.isArray(d) ? d : (d.data || []);
}
$needle$,
           $replacement$async function fetchJson(url){
  const r = await fetch(url);
  if(!r.ok) throw new Error(url + ' (' + r.status + ')');
  const d = await r.json();
  return Array.isArray(d) ? d : (d.data || []);
}
async function fetchOptional(url){
  try { return await fetchJson(url); }
  catch(e) { console.warn('dataset opcional no disponible', url, e.message); return []; }
}
$replacement$
         ),
         $needle$fetchJson('/api/data/sap_successfactors_employees_anomalies')$needle$,
         $replacement$fetchOptional('/api/data/sap_successfactors_employees_anomalies')$replacement$
       ),
       $needle$fetchJson('/api/data/sap_successfactors_recruitment_funnel')$needle$,
       $replacement$fetchOptional('/api/data/sap_successfactors_recruitment_funnel')$replacement$
     ),
       updated_at = NOW()
 WHERE name = 'sap_successfactors_talent_health'
   AND html NOT LIKE '%fetchOptional%';

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99m_sap_successfactors_apps_optional_datasets.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
