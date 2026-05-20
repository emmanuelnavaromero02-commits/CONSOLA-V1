-- 1) Cartucho meta `platform`: aloja DAGs compartidos entre cartuchos
--    (file_ingest, entity_scheduler, dataset_refresh_chain, agent_runner). No contiene datos propios.
INSERT INTO cartridges (id, name, version, description, pattern, category)
VALUES ('platform', 'Platform', '1.0.0',
        'DAGs y meta-funciones compartidos entre cartuchos. No contiene datos propios.',
        'meta', 'platform')
ON CONFLICT (id) DO NOTHING;


-- 2) Rol del DAG en el modelo:
--    worker       — recibe `entity` por conf y ejecuta UN trabajo (aparece
--                   en el selector de DAG del Paso 3 ENTIDADES).
--    orchestrator — corre su propio loop, dispara otros DAGs (entity_scheduler,
--                   agent_runner). NO se asigna a entidad.
--    utility      — helper / debug (listar inbox, validar). NO se asigna a entidad.
ALTER TABLE cartridge_dags
    ADD COLUMN IF NOT EXISTS dag_role TEXT NOT NULL DEFAULT 'worker';

-- Constraint check separado para que sea idempotente
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'cartridge_dags_dag_role_check'
    ) THEN
        ALTER TABLE cartridge_dags
            ADD CONSTRAINT cartridge_dags_dag_role_check
            CHECK (dag_role IN ('worker', 'orchestrator', 'utility'));
    END IF;
END $$;


-- 3) Mover DAGs cartridge-agnostic al cartucho platform (idempotente: si ya hay
--    row en platform, se borra el otro; si no, se mueve).
DO $$
DECLARE
    d TEXT;
BEGIN
    FOREACH d IN ARRAY ARRAY['file_ingest', 'entity_scheduler', 'dataset_refresh_chain', 'agent_runner']
    LOOP
        UPDATE cartridge_dags
           SET cartridge_id = 'platform'
         WHERE dag_id = d
           AND cartridge_id <> 'platform'
           AND NOT EXISTS (
               SELECT 1 FROM cartridge_dags
                WHERE cartridge_id = 'platform' AND dag_id = d
           );
        DELETE FROM cartridge_dags
         WHERE dag_id = d AND cartridge_id <> 'platform';
    END LOOP;
END $$;


-- 4) Clasificar roles
UPDATE cartridge_dags SET dag_role = 'orchestrator'
 WHERE dag_id IN ('entity_scheduler', 'dataset_refresh_chain', 'agent_runner');

UPDATE cartridge_dags SET dag_role = 'utility'
 WHERE dag_id IN ('replicon_check_input_files');

-- Lo demás (replicon_extract, file_ingest, etc.) queda como 'worker' por default.
