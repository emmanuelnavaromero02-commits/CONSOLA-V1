-- ─────────────────────────────────────────────────────────────────────────────
-- MODecissions Cartridge: SAP S/4HANA Core — seed configuration
-- Run once to register this cartridge in a new installation.
-- Safe to re-run: all inserts use ON CONFLICT DO NOTHING / DO UPDATE.
-- ─────────────────────────────────────────────────────────────────────────────

-- ── Cartridge header ──────────────────────────────────────────────────────────
INSERT INTO cartridges (id, name, version, description, pattern, category, bronze_path)
VALUES (
    'sap_s4hana',
    'SAP S/4HANA Core',
    '1.0.0',
    'SAP S/4HANA on-premise / S4HANA — extrae datos maestros de empleados, estructura organizacional, ausencias y horarios.',
    'dag-based',
    'cartridge',
    'raw/sap_s4hana/{entity}/load_date={date}/'
)
ON CONFLICT (id) DO UPDATE
    SET name        = EXCLUDED.name,
        version     = EXCLUDED.version,
        description = EXCLUDED.description,
        updated_at  = NOW();

-- ── DAGs ──────────────────────────────────────────────────────────────────────
INSERT INTO cartridge_dags (cartridge_id, dag_id, file, description, trigger, params)
VALUES
    ('sap_s4hana', 'sap_s4hana_extract',     'sap_s4hana_extract.py',     'Extrae una entidad en Bronze MinIO (full o incremental)',  'on-demand', '["entity","mode","from_date","to_date"]'),
    ('sap_s4hana', 'sap_s4hana_extract_all', 'sap_s4hana_extract_all.py', 'Extrae todas las entidades habilitadas en secuencia',       'on-demand', '["mode","entities"]')
ON CONFLICT (cartridge_id, dag_id) DO NOTHING;

-- ── Entities ──────────────────────────────────────────────────────────────────
-- The real S/4HANA OData entities, mirroring infra/init/77_sap_entity_alignment.sql
-- and cartridges/sap_s4hana/app/config/entities.yaml. Previously this block held a
-- clone of the SAP HCM business entities (EmployeeMaster, PersonalData, ...), which
-- never matched entities.yaml; re-importing the cartridge re-seeded those junk rows
-- and undid migration 77's alignment. This UPSERT now seeds the 25 correct rows on
-- (cartridge_id, entity); any leftover HCM rows are pruned by migration 77 (a fresh
-- install has none). Metadata (odata_entity / mode / watermark / page_size /
-- date_field) is inherited from entities.yaml.
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

-- ── Semantic vocabulary ───────────────────────────────────────────────────────
INSERT INTO semantic_terms (cartridge_id, term, definition, maps_to)
VALUES
    ('sap_s4hana', 'headcount activo', 'Número de empleados activos hoy', 'EmployeeMaster WHERE Endda >= CURRENT_DATE AND Begda <= CURRENT_DATE'),
    ('sap_s4hana', 'ausencia', 'Días de ausencia', 'LeaveAbsence.Abwtg')
ON CONFLICT (cartridge_id, term) DO NOTHING;
