\set ON_ERROR_STOP on
INSERT INTO entity_watermarks
    (cartridge_id, entity_name, watermark_field, last_watermark_value, last_run_id,
     tenant_id, workspace_id, watermark_scope)
SELECT 'sap_b1', e.entity || '@' || c.alias, e.watermark_field, :'cutoff', 'initial-load',
       :'tenant_id'::uuid, :'workspace_id'::uuid,
       'tenant:' || :'tenant_id' || ':workspace:' || :'workspace_id'
FROM entity_config AS e
CROSS JOIN unnest(string_to_array(:'aliases', ',')) AS c(alias)
WHERE e.cartridge_id = 'sap_b1'
  AND e.watermark_format = 'b1_update_ts'
ON CONFLICT (watermark_scope, cartridge_id, entity_name) DO NOTHING;
