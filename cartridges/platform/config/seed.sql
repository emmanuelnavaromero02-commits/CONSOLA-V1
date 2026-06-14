-- Platform cartridge — shared orchestration/meta DAGs used by every cartridge.
-- It owns no data and connects to no external system. Registered as a
-- default-enabled cartridge so it is active for every tenant automatically.
--
-- Idempotent: safe to re-run on every boot / fresh install.

INSERT INTO cartridges (id, name, version, pattern, category, description)
VALUES (
  'platform',
  'Platform',
  '1.0.0',
  'meta',
  'platform',
  'DAGs y meta-funciones compartidos entre cartuchos. No contiene datos propios.'
)
ON CONFLICT (id) DO UPDATE SET
  name        = EXCLUDED.name,
  version     = EXCLUDED.version,
  pattern     = EXCLUDED.pattern,
  category    = EXCLUDED.category,
  description = EXCLUDED.description,
  updated_at  = now();

-- Shared DAGs. entity_scheduler is the meta-scheduler that fans one base DAG
-- out over many entities (conf.entity/mode/cartridge_id); the others are
-- platform-level orchestration/ingest helpers any cartridge can reuse.
INSERT INTO cartridge_dags (cartridge_id, dag_id, file, description, trigger, dag_role)
VALUES
  ('platform', 'entity_scheduler',      'entity_scheduler.py',      'Meta-scheduler: lee entity_config y dispara DAGs base por entidad según cron.', 'scheduled', 'orchestrator'),
  ('platform', 'agent_runner',          'agent_runner.py',          'Invoca agentes cuya extra.schedule.cron caiga dentro del intervalo actual.',     'scheduled', 'orchestrator'),
  ('platform', 'dataset_refresh_chain', 'dataset_refresh_chain.py', 'Orquestador: propaga materializaciones aguas abajo según datasets.sources.',     'on-demand', 'orchestrator'),
  ('platform', 'file_ingest',           'file_ingest.py',           'Ingesta genérica de archivos (csv/excel) del lake → parquet bronze.',            'on-demand', 'worker')
ON CONFLICT (cartridge_id, dag_id) DO UPDATE SET
  file        = EXCLUDED.file,
  description = EXCLUDED.description,
  trigger     = EXCLUDED.trigger,
  dag_role    = EXCLUDED.dag_role,
  updated_at  = now();
