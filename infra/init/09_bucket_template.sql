-- Migración: reemplaza buckets concretos en sql_def por placeholder {bucket}
-- Idempotente. Tras esto, los datasets son portables entre local/AWS.

UPDATE datasets
   SET sql_def = REGEXP_REPLACE(sql_def, 's3://[^/]+/', 's3://{bucket}/', 'g')
 WHERE sql_def LIKE '%s3://%';
