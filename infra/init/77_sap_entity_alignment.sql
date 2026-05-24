-- 77_sap_entity_alignment.sql
--
-- Align sap_s4hana's entity_config with its real OData contract.
--
-- Problem: sap_s4hana's entity_config (seeded by cartridges/sap_s4hana/
-- config/seed.sql) was a copy-paste of the SAP HCM business entities
-- (EmployeeMaster, PersonalData, ContractData, OrgUnit, Position, CostCenter,
-- JobCode, EmployeeActions, LeaveAbsence, WorkSchedule). None of those exist in
-- cartridges/sap_s4hana/app/config/entities.yaml, so an extraction request for
-- e.g. EmployeeMaster reached the cartridge, found no OData mapping, and 404'd.
-- Meanwhile sap_s4hana's own knowledge_bits (SalesOrder, BillingDocument,
-- PurchaseOrder, GLAccount) already expected the real ERP entities.
--
-- Fix (data-only, no cartridge code change): the extractor resolves the OData
-- path via config.get("odata_entity", entity), and catalog_service treats the
-- DB's odata_entity as authoritative (entities.yaml only fills gaps). So we add
-- the odata_entity column and seed the real S/4HANA entities, inheriting
-- odata_entity / mode / watermark_field / page_size / date_field from
-- entities.yaml. Business entity names are kept (they are the bronze path
-- component raw/sap_s4hana/{entity}/ and what the KBs read).
--
-- Scope: sap_s4hana ONLY. sap_hcm and sap_successfactors have unresolved
-- entity-name / entities.yaml mismatches and KB coupling and are deferred to a
-- separate change. Replicon is untouched (filtered by cartridge_id).
--
-- Known residual (out of scope here): cartridges/sap_s4hana/config/seed.sql
-- still carries the old HCM rows with ON CONFLICT DO UPDATE, so re-importing
-- the cartridge re-adds those 9 junk rows (without odata_entity). Fixing that
-- requires editing the cartridge seed, which is a separate PR.

ALTER TABLE entity_config ADD COLUMN IF NOT EXISTS odata_entity TEXT;

-- Drop the HCM rows mis-seeded under sap_s4hana. Only rows that are NOT part of
-- the real S/4HANA entity set are removed; the valid ones are upserted below.
DELETE FROM entity_config
 WHERE cartridge_id = 'sap_s4hana'
   AND entity NOT IN (
     'BusinessPartner', 'Customer', 'Supplier', 'BusinessPartnerAddress', 'Product',
     'ProductDescription', 'CompanyCode', 'CostCenter', 'ProfitCenter', 'GLAccount',
     'SalesOrder', 'SalesOrderItem', 'SalesOrderScheduleLine', 'BillingDocument',
     'BillingDocumentItem', 'PurchaseOrder', 'PurchaseOrderItem',
     'PurchaseRequisitionHeader', 'PurchaseRequisitionItem', 'SupplierInvoice',
     'GLAccountLineItem', 'OPLAcctgDocItemCube', 'JournalEntryItem',
     'MatlStkInAcctMod', 'MaterialDocumentHeader'
   );

INSERT INTO entity_config
    (cartridge_id, entity, odata_entity, display_name, description, mode,
     watermark_field, watermark_format, page_size, date_field, dag_id, enabled, trigger_type)
VALUES
    ('sap_s4hana', 'BusinessPartner', 'API_BUSINESS_PARTNER/A_BusinessPartner', 'BusinessPartner', 'Business Partner header (customers, vendors, employees).', 'incremental', 'LastChangeDate', 'iso8601', 500, NULL, 'sap_s4hana_extract', TRUE, 'manual'),
    ('sap_s4hana', 'Customer', 'API_BUSINESS_PARTNER/A_Customer', 'Customer', 'Customer master.', 'incremental', 'LastChangeDate', NULL, 500, NULL, 'sap_s4hana_extract', TRUE, 'manual'),
    ('sap_s4hana', 'Supplier', 'API_BUSINESS_PARTNER/A_Supplier', 'Supplier', 'Supplier (vendor) master.', 'incremental', 'LastChangeDate', NULL, 500, NULL, 'sap_s4hana_extract', TRUE, 'manual'),
    ('sap_s4hana', 'BusinessPartnerAddress', 'API_BUSINESS_PARTNER/A_BusinessPartnerAddress', 'BusinessPartnerAddress', 'Business partner addresses.', 'incremental', 'LastChangeDate', NULL, 1000, NULL, 'sap_s4hana_extract', TRUE, 'manual'),
    ('sap_s4hana', 'Product', 'API_PRODUCT_SRV/A_Product', 'Product', 'Product (material) master.', 'incremental', 'LastChangeDateTime', NULL, 500, NULL, 'sap_s4hana_extract', TRUE, 'manual'),
    ('sap_s4hana', 'ProductDescription', 'API_PRODUCT_SRV/A_ProductDescription', 'ProductDescription', 'Product descriptions per language.', 'full', NULL, NULL, 1000, NULL, 'sap_s4hana_extract', TRUE, 'manual'),
    ('sap_s4hana', 'CompanyCode', 'API_COMPANYCODE_SRV/A_CompanyCode', 'CompanyCode', 'Company code master.', 'full', NULL, NULL, 1000, NULL, 'sap_s4hana_extract', TRUE, 'manual'),
    ('sap_s4hana', 'CostCenter', 'API_COSTCENTER_SRV/A_CostCenter', 'CostCenter', 'Cost centers.', 'incremental', 'LastChangeDateTime', NULL, 1000, NULL, 'sap_s4hana_extract', TRUE, 'manual'),
    ('sap_s4hana', 'ProfitCenter', 'API_PROFITCENTER_SRV/A_ProfitCenter', 'ProfitCenter', 'Profit centers.', 'incremental', 'LastChangeDateTime', NULL, 1000, NULL, 'sap_s4hana_extract', TRUE, 'manual'),
    ('sap_s4hana', 'GLAccount', 'API_GLACCOUNT_SRV/A_GLAccount', 'GLAccount', 'G/L account master (chart of accounts).', 'full', NULL, NULL, 1000, NULL, 'sap_s4hana_extract', TRUE, 'manual'),
    ('sap_s4hana', 'SalesOrder', 'API_SALES_ORDER_SRV/A_SalesOrder', 'SalesOrder', 'Sales order header.', 'incremental', 'LastChangeDateTime', NULL, 500, 'SalesOrderDate', 'sap_s4hana_extract', TRUE, 'manual'),
    ('sap_s4hana', 'SalesOrderItem', 'API_SALES_ORDER_SRV/A_SalesOrderItem', 'SalesOrderItem', 'Sales order line items.', 'incremental', 'LastChangeDateTime', NULL, 1000, NULL, 'sap_s4hana_extract', TRUE, 'manual'),
    ('sap_s4hana', 'SalesOrderScheduleLine', 'API_SALES_ORDER_SRV/A_SalesOrderScheduleLine', 'SalesOrderScheduleLine', 'Sales order schedule lines (delivery dates).', 'incremental', 'LastChangeDateTime', NULL, 1000, NULL, 'sap_s4hana_extract', TRUE, 'manual'),
    ('sap_s4hana', 'BillingDocument', 'API_BILLING_DOCUMENT_SRV/A_BillingDocument', 'BillingDocument', 'Billing document header (invoice header).', 'incremental', 'LastChangeDateTime', NULL, 500, 'BillingDocumentDate', 'sap_s4hana_extract', TRUE, 'manual'),
    ('sap_s4hana', 'BillingDocumentItem', 'API_BILLING_DOCUMENT_SRV/A_BillingDocumentItem', 'BillingDocumentItem', 'Billing document items.', 'incremental', 'LastChangeDateTime', NULL, 1000, NULL, 'sap_s4hana_extract', TRUE, 'manual'),
    ('sap_s4hana', 'PurchaseOrder', 'API_PURCHASEORDER_PROCESS_SRV/A_PurchaseOrder', 'PurchaseOrder', 'Purchase order header.', 'incremental', 'LastChangeDateTime', NULL, 500, 'PurchaseOrderDate', 'sap_s4hana_extract', TRUE, 'manual'),
    ('sap_s4hana', 'PurchaseOrderItem', 'API_PURCHASEORDER_PROCESS_SRV/A_PurchaseOrderItem', 'PurchaseOrderItem', 'Purchase order line items.', 'incremental', 'LastChangeDateTime', NULL, 1000, NULL, 'sap_s4hana_extract', TRUE, 'manual'),
    ('sap_s4hana', 'PurchaseRequisitionHeader', 'API_PURCHASEREQ_PROCESS_SRV/A_PurchaseRequisitionHeader', 'PurchaseRequisitionHeader', 'Purchase requisition header.', 'incremental', 'LastChangeDateTime', NULL, 500, NULL, 'sap_s4hana_extract', TRUE, 'manual'),
    ('sap_s4hana', 'PurchaseRequisitionItem', 'API_PURCHASEREQ_PROCESS_SRV/A_PurchaseRequisitionItem', 'PurchaseRequisitionItem', 'Purchase requisition line items.', 'incremental', 'LastChangeDateTime', NULL, 1000, NULL, 'sap_s4hana_extract', TRUE, 'manual'),
    ('sap_s4hana', 'SupplierInvoice', 'API_SUPPLIERINVOICE_PROCESS_SRV/A_SupplierInvoice', 'SupplierInvoice', 'Supplier invoice header (vendor invoice).', 'incremental', 'LastChangeDateTime', NULL, 500, NULL, 'sap_s4hana_extract', TRUE, 'manual'),
    ('sap_s4hana', 'GLAccountLineItem', 'API_GLACCOUNTLINEITEM_SRV/YY1_GLAccountLineItem', 'GLAccountLineItem', 'G/L account line items (FI ledger detail).', 'incremental', 'LastChangeDate', NULL, 1000, 'PostingDate', 'sap_s4hana_extract', TRUE, 'manual'),
    ('sap_s4hana', 'OPLAcctgDocItemCube', 'API_OPLACCTGDOCITEMCUBE_SRV/YY1_OPLAcctgDocItemCube', 'OPLAcctgDocItemCube', 'Operational accounting document cube (matrix view of FI postings).', 'incremental', 'LastChangeDate', NULL, 1000, 'PostingDate', 'sap_s4hana_extract', TRUE, 'manual'),
    ('sap_s4hana', 'JournalEntryItem', 'API_JOURNALENTRYITEM_SRV/A_JournalEntryItem', 'JournalEntryItem', 'Universal Journal entry items (ACDOCA).', 'incremental', 'LastChangeDateTime', NULL, 2000, 'PostingDate', 'sap_s4hana_extract', TRUE, 'manual'),
    ('sap_s4hana', 'MatlStkInAcctMod', 'API_MATERIAL_STOCK_SRV/A_MatlStkInAcctMod', 'MatlStkInAcctMod', 'Material stock by account modification.', 'full', NULL, NULL, 1000, NULL, 'sap_s4hana_extract', TRUE, 'manual'),
    ('sap_s4hana', 'MaterialDocumentHeader', 'API_MATERIAL_DOCUMENT_SRV/A_MaterialDocumentHeader', 'MaterialDocumentHeader', 'Material document header (goods movements).', 'incremental', 'LastChangeDateTime', NULL, 1000, 'PostingDate', 'sap_s4hana_extract', TRUE, 'manual')
ON CONFLICT (cartridge_id, entity) DO UPDATE
    SET odata_entity     = EXCLUDED.odata_entity,
        display_name     = EXCLUDED.display_name,
        description      = EXCLUDED.description,
        mode             = EXCLUDED.mode,
        watermark_field  = EXCLUDED.watermark_field,
        watermark_format = EXCLUDED.watermark_format,
        page_size        = EXCLUDED.page_size,
        date_field       = EXCLUDED.date_field,
        dag_id           = EXCLUDED.dag_id,
        enabled          = EXCLUDED.enabled,
        trigger_type     = EXCLUDED.trigger_type;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('77_sap_entity_alignment.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
