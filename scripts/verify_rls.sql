-- verify_rls.sql — auditoria de RLS tenant-scoped para un operador.
-- Uso: psql -U postgres -d <db> -f scripts/verify_rls.sql
-- Lista toda tabla base de 'public' con columna workspace_id y su estado de
-- RLS. Una tabla tenant-scoped SANA tiene rls=t Y force=t. Cualquier fila con
-- force=f es un hueco de aislamiento multi-tenant que hay que corregir
-- aplicando las migraciones (make migrate / apply_db_migrations.sh).
SELECT c.relname                     AS tabla,
       c.relrowsecurity              AS rls,
       c.relforcerowsecurity         AS force,
       CASE WHEN c.relrowsecurity AND c.relforcerowsecurity
            THEN 'OK' ELSE 'FALTA_FORCE_RLS' END AS estado
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_attribute a ON a.attrelid = c.oid
   AND a.attname = 'workspace_id' AND a.attnum > 0 AND NOT a.attisdropped
 WHERE c.relkind = 'r' AND n.nspname = 'public'
 ORDER BY estado DESC, c.relname;
