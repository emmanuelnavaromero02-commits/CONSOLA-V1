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
