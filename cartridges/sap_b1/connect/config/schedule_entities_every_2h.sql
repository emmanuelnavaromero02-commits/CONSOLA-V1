\set ON_ERROR_STOP on

SELECT COUNT(*) > 0 AS sap_b1_seeded
FROM entity_config
WHERE cartridge_id = 'sap_b1' \gset
\if :sap_b1_seeded
\else
  \echo 'entity_config has no sap_b1 rows: start the cartridge once so it seeds them, then re-run.'
  \quit 1
\endif

SELECT EXISTS (
    SELECT 1 FROM workspaces
    WHERE id = :'workspace_id'::uuid AND tenant_id = :'tenant_id'::uuid
) AS scope_exists \gset
\if :scope_exists
\else
  \echo 'workspace_id does not belong to tenant_id (or does not exist): nothing changed.'
  \quit 1
\endif

BEGIN;

UPDATE entity_config
SET trigger_type    = 'scheduled',
    dag_id          = COALESCE(NULLIF(dag_id, ''), 'sap_b1_extract'),
    connection_id   = COALESCE(NULLIF(connection_id, ''), 'sap_b1'),
    tenant_id       = :'tenant_id'::uuid,
    workspace_id    = :'workspace_id'::uuid,
    cron_expression = CASE
        WHEN entity IN ('CINF','OADM','OCRN','ORTT','OACT','OFPR','OPRC','OCRG','OSLP','OWHS','OITB','OCRD','OITM','OITT','ITT1')
            THEN '0 */2 * * *'
        WHEN entity IN ('OINV','INV1','ORIN','RIN1','ODLN','DLN1','ORDN','RDN1','ORDR','RDR1')
            THEN '10 */2 * * *'
        WHEN entity IN ('OPCH','PCH1','ORPC','RPC1','OPDN','PDN1','OPOR','POR1')
            THEN '20 */2 * * *'
        WHEN entity IN ('OJDT','JDT1')
            THEN '30 */2 * * *'
        WHEN entity IN ('OWTR','WTR1','OINM','IBT1','OITW','OBTN','OBTQ','OIBT')
            THEN '40 */2 * * *'
        WHEN entity IN ('OWOR','WOR1')
            THEN '50 */2 * * *'
        ELSE '0 */2 * * *'
    END
WHERE cartridge_id = 'sap_b1';


COMMIT;

SELECT entity, mode, enabled, trigger_type, cron_expression, dag_id, last_scheduled_at
FROM entity_config
WHERE cartridge_id = 'sap_b1'
ORDER BY cron_expression, entity;

