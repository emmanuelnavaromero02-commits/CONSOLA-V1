-- Ensure shared platform DAGs exist even on installs where
-- 20_dag_role_and_platform.sql ran before those DAG rows were seeded.

INSERT INTO cartridges (id, name, version, description, pattern, category)
VALUES ('platform', 'Platform', '1.0.0',
        'DAGs y meta-funciones compartidos entre cartuchos. No contiene datos propios.',
        'meta', 'platform')
ON CONFLICT (id) DO NOTHING;

INSERT INTO cartridge_dags
    (cartridge_id, dag_id, file, description, trigger, params, dag_role, dag_params_example)
VALUES
    ('platform', 'file_ingest', 'file_ingest.py',
     'Ingesta genérica de archivos (csv/excel) del lake → parquet bronze.',
     'on-demand',
     '["entity","cartridge_id","mode","file_pattern","format","delimiter","encoding","sheet","skiprows","parser"]'::jsonb,
     'worker',
     '{"file_pattern":"*.csv","format":"csv","delimiter":",","encoding":"utf-8"}'::jsonb),
    ('platform', 'entity_scheduler', 'entity_scheduler.py',
     'Meta-scheduler: lee entity_config y dispara DAGs base por entidad según cron.',
     'scheduled',
     '[]'::jsonb,
     'orchestrator',
     '{}'::jsonb),
    ('platform', 'dataset_refresh_chain', 'dataset_refresh_chain.py',
     'Orquestador: propaga materializaciones aguas abajo según datasets.sources.',
     'on-demand',
     '["seed_raw","seed_dataset","max_depth"]'::jsonb,
     'orchestrator',
     '{"seed_raw":"raw/replicon/ProjectAudit","max_depth":5}'::jsonb),
    ('platform', 'agent_runner', 'agent_runner.py',
     'Invoca agentes cuya extra.schedule.cron caiga dentro del intervalo actual.',
     'scheduled',
     '[]'::jsonb,
     'orchestrator',
     '{"message":"Ejecuta tu tarea programada."}'::jsonb)
ON CONFLICT (cartridge_id, dag_id) DO UPDATE
   SET file = EXCLUDED.file,
       description = EXCLUDED.description,
       trigger = EXCLUDED.trigger,
       params = EXCLUDED.params,
       dag_role = EXCLUDED.dag_role,
       dag_params_example = EXCLUDED.dag_params_example,
       updated_at = NOW();
