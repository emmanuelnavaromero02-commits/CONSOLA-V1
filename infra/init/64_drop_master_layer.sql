-- Consolidación a 3 capas: raw / silver / gold.
-- Master desaparece como concepto. Lo que vivía en master era modelado
-- dimensional con joins/enriquecimiento — encaja en gold (star schema:
-- dim_* y fact_* viven juntos en gold). Así el grafo queda limpio:
-- gold depende solo de silver o gold, sin saltos hacia abajo.

-- 1) Reasignar layer de master → gold
UPDATE datasets
   SET layer      = 'gold',
       updated_at = NOW()
 WHERE layer = 'master';

-- 2) Reescribir referencias 'master_X' → 'gold_X' en sources JSONB
UPDATE datasets
   SET sources = (
        SELECT jsonb_agg(
            CASE
                WHEN value::text LIKE '"master_%'
                    THEN to_jsonb('gold_' || substring(value #>> '{}' from 8))
                ELSE value
            END
        )
        FROM jsonb_array_elements(sources)
       )
 WHERE sources::text LIKE '%master_%';

-- 3) Renombrar tablas físicas en pggold si existen
DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT schemaname, tablename
          FROM pg_catalog.pg_tables
         WHERE tablename LIKE 'master_%'
    LOOP
        EXECUTE format('ALTER TABLE %I.%I RENAME TO %I',
                       r.schemaname,
                       r.tablename,
                       'gold_' || substring(r.tablename FROM 8));
    END LOOP;
END $$;
