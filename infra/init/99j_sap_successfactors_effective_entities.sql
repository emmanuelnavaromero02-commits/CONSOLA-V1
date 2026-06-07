-- 99j_sap_successfactors_effective_entities.sql
--
-- Live FEMSA SuccessFactors hardening:
-- - Add PaymentInformationDetailV3 to the cartridge catalog.
-- - Persist OData v2 select/protection metadata for the additional entities
--   enabled after PerPerson.
-- - Mark effective-dated entities so the extractor sends fromDate/toDate to SF.
--
-- Idempotent and cartridge-scoped. Existing admin edits outside these specific
-- extraction metadata fields are preserved.

INSERT INTO entity_config
    (cartridge_id, entity, odata_entity, display_name, description, mode,
     watermark_field, page_size, primary_key, dag_id, enabled, trigger_type)
VALUES
    ('sap_successfactors', 'PaymentInformationDetailV3', 'PaymentInformationDetailV3',
     'Detalle de Pago V3', 'Detalles bancarios / metodo de pago (PaymentInformationDetailV3)',
     'incremental', 'lastModifiedDateTime', 200, 'externalCode',
     'sap_successfactors_extract', TRUE, 'manual')
ON CONFLICT (cartridge_id, entity) DO UPDATE
    SET odata_entity    = EXCLUDED.odata_entity,
        display_name    = EXCLUDED.display_name,
        description     = EXCLUDED.description,
        mode            = EXCLUDED.mode,
        watermark_field = EXCLUDED.watermark_field,
        page_size       = EXCLUDED.page_size,
        primary_key     = EXCLUDED.primary_key,
        dag_id          = EXCLUDED.dag_id,
        enabled         = EXCLUDED.enabled,
        trigger_type    = EXCLUDED.trigger_type;

UPDATE entity_config
   SET effective_dated = TRUE,
       date_field = 'startDate',
       select_fields = '["personIdExternal","startDate","endDate","firstName","middleName","lastName","gender","maritalStatus","nationality","lastModifiedDateTime"]'::jsonb,
       protection = '{"firstName":"masked","lastName":"masked","gender":"plain","maritalStatus":"plain"}'::jsonb
 WHERE cartridge_id = 'sap_successfactors'
   AND entity = 'PerPersonal';

UPDATE entity_config
   SET select_fields = '["personIdExternal","emailType","emailAddress","isPrimary","lastModifiedDateTime"]'::jsonb,
       protection = '{"emailAddress":"masked"}'::jsonb
 WHERE cartridge_id = 'sap_successfactors'
   AND entity = 'PerEmail';

UPDATE entity_config
   SET effective_dated = FALSE,
       date_field = NULL,
       select_fields = '["personIdExternal","userId","startDate","endDate","assignmentClass","originalStartDate","lastModifiedDateTime"]'::jsonb
 WHERE cartridge_id = 'sap_successfactors'
   AND entity = 'EmpEmployment';

UPDATE entity_config
   SET effective_dated = TRUE,
       date_field = 'startDate',
       select_fields = '["userId","startDate","endDate","jobCode","position","department","division","location","businessUnit","company","costCenter","managerId","eventReason","lastModifiedDateTime"]'::jsonb
 WHERE cartridge_id = 'sap_successfactors'
   AND entity = 'EmpJob';

UPDATE entity_config
   SET select_fields = '["externalCode","PaymentInformationV3_worker","PaymentInformationV3_effectiveStartDate","mdfSystemEffectiveStartDate","mdfSystemEffectiveEndDate","paymentMethod","bankCountry","bank","businessIdentifierCode","routingNumber","accountNumber","accountOwner","iban","currency","amount","percent","payType","customPayType","paySequence","purpose","lastModifiedDateTime"]'::jsonb,
       protection = '{"accountNumber":"encrypted","accountOwner":"masked","iban":"encrypted","routingNumber":"encrypted","businessIdentifierCode":"encrypted"}'::jsonb
 WHERE cartridge_id = 'sap_successfactors'
   AND entity = 'PaymentInformationDetailV3';

UPDATE entity_config
   SET effective_dated = FALSE,
       date_field = NULL,
       select_fields = '["externalCode","name","status","startDate","endDate","lastModifiedDateTime"]'::jsonb
 WHERE cartridge_id = 'sap_successfactors'
   AND entity = 'FOLocation';

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99j_sap_successfactors_effective_entities.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
