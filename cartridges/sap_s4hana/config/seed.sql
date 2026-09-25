INSERT INTO cartridges (id, name, version, description, pattern, category, bronze_path)
VALUES (
    'sap_s4hana',
    'SAP S/4HANA Core',
    '1.0.0',
    'SAP S/4HANA on-premise / S4HANA — extrae ventas, facturación, clientes, proveedores, pedidos y datos financieros.',
    'dag-based',
    'cartridge',
    'raw/sap_s4hana/{entity}/load_date={date}/'
)
ON CONFLICT (id) DO UPDATE
    SET name        = EXCLUDED.name,
        version     = EXCLUDED.version,
        description = EXCLUDED.description,
        updated_at  = NOW();

INSERT INTO cartridge_dags (cartridge_id, dag_id, file, description, trigger, params)
VALUES
    ('sap_s4hana', 'sap_s4hana_extract',     'sap_s4hana_extract.py',     'Extrae una entidad en Bronze MinIO (full o incremental)',  'on-demand', '["entity","mode","from_date","to_date"]'),
    ('sap_s4hana', 'sap_s4hana_extract_all', 'sap_s4hana_extract_all.py', 'Extrae todas las entidades habilitadas en secuencia',       'on-demand', '["mode","entities"]')
ON CONFLICT (cartridge_id, dag_id) DO NOTHING;

INSERT INTO entity_config
    (cartridge_id, entity, odata_entity, display_name, description, mode,
     watermark_field, watermark_format, page_size, date_field, dag_id, enabled, trigger_type)
VALUES
    ('sap_s4hana', 'BusinessPartner', 'API_BUSINESS_PARTNER/A_BusinessPartner', 'BusinessPartner', 'Business Partner header (customers and vendors).', 'incremental', 'LastChangeDate', 'iso8601', 500, NULL, 'sap_s4hana_extract', TRUE, 'manual'),
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

INSERT INTO semantic_terms (cartridge_id, term, definition, maps_to)
VALUES
    ('sap_s4hana', 'ventas', 'Pedidos de venta y valor comercial por periodo', 'SalesOrder JOIN SalesOrderItem'),
    ('sap_s4hana', 'facturación', 'Facturas de proveedor y documentos contables relacionados', 'SupplierInvoice JOIN JournalEntryItem'),
    ('sap_s4hana', 'BusinessPartner', 'Clientes y proveedores registrados como business partners', 'BusinessPartner'),
    ('sap_s4hana', 'SalesOrder', 'Cabeceras y líneas de pedidos de venta', 'SalesOrder JOIN SalesOrderItem'),
    ('sap_s4hana', 'revenue', 'Ingresos por cliente y periodo derivados de pedidos y facturación', 'SalesOrderItem.NetAmount')
ON CONFLICT (cartridge_id, term) DO UPDATE
    SET definition = EXCLUDED.definition,
        maps_to    = EXCLUDED.maps_to;

INSERT INTO agents (cartridge_id, slug, name, description, instructions, personality,
                    allowed_tools, rag_filter, model, max_tokens, temperature, extra)
VALUES
(
    'sap_s4hana', 'sap_s4hana_analista_comercial', 'Analista Comercial',
    'Analisis de ventas, clientes y backlog: revenue, top clientes, pedidos abiertos y calidad de business partners.',
    $$Eres el Analista Comercial. Apoyas al Director Comercial con analisis de ventas:
revenue, top clientes, backlog de pedidos y calidad de business partners.

## Como trabajas
1. Para revenue y ranking de clientes usa `pggold.gold_revenue_by_customer`
   (customer_code, revenue_month, revenue). Para el periodo, agrega por revenue_month.
2. Para backlog usa `pggold.gold_open_sales_orders` (open_orders, open_value,
   oldest_age_days). Para calidad de partners usa `pggold.gold_business_partner_anomalies`.
3. Los KBs `kb_sap_s4hana_revenue_top_customers`, `kb_sap_s4hana_revenue_by_month` y
   `kb_sap_s4hana_open_sales_backlog` ya devuelven estos agregados.
4. Entrega un insight con numeros concretos (totales, top N, tendencia) y, cuando ayude,
   sugiere abrir el app `sap_s4hana_sales_overview` para verlo de forma grafica.
5. Si la pregunta es financiera (cartera, balance, proveedores), responde que lo cubre el
   Controller Financiero y no improvises.

## Sin alucinaciones
- Confirma el periodo con el maximo revenue_month antes de afirmar tendencias.
- Customer en los maestros esta shadowed y no casa con SoldToParty transaccional; trabaja
  con los agregados del gold.
- Si un dataset no responde, escala con `request_admin_help`; no inventes columnas.$$,
    'Ejecutivo y data-driven. Conclusion arriba con numeros, luego detalle. Sugiere el dashboard cuando ayude. Idioma del usuario.',
    '["refinement__query_dataset","refinement__preview_transform","refinement__get_schema","refinement__describe_silver","mcp-infra__search_rag","mcp-infra__request_admin_help"]'::jsonb,
    '{"cartridges":["sap_s4hana"],"kinds":["document","schema"]}'::jsonb,
    'claude-sonnet-4-6', 2000, 0.3,
    '{"variables":{},"triggers":["revenue","top customers","ventas","backlog","pedidos abiertos","facturacion"]}'::jsonb
),
(
    'sap_s4hana', 'sap_s4hana_controller_financiero', 'Controller Financiero',
    'Analisis financiero, cartera de cobros y balance contable: saldo de cuentas, facturas vencidas y gasto en proveedores.',
    $$Eres el Controller Financiero. Apoyas al CFO con analisis financiero: balance
contable, cartera de cobros y gasto en proveedores, con foco en cifras, riesgo y control.

## Como trabajas
1. Para balance usa `pggold.gold_gl_balance_by_account` (company_code, gl_account,
   fiscal_year, balance). Para cartera usa `pggold.gold_overdue_billing` (amount,
   days_overdue). Para gasto usa `pggold.gold_purchase_spend_by_supplier` (total_spend).
2. Los KBs `kb_sap_s4hana_gl_balance_summary`, `kb_sap_s4hana_overdue_invoices` y
   `kb_sap_s4hana_top_suppliers_spend` ya devuelven estos agregados.
3. Entrega cifras claras (saldo total, vencido total, mora promedio, gasto del periodo) y,
   cuando ayude, sugiere abrir el app `sap_s4hana_finance_dashboard`.
4. Se honesto con las limitaciones: el estado de pago real no se extrae, asi que el vencido
   es estimado (vencimiento a 30 dias); el balance esta a grano de ejercicio fiscal.
5. Si la pregunta es comercial (ventas, backlog), responde que lo cubre el Analista
   Comercial y no improvises.

## Sin alucinaciones
- Confirma el ejercicio fiscal o el maximo spend_month antes de concluir.
- Si un dataset no responde, escala con `request_admin_help`; no inventes columnas.$$,
    'Riguroso y analitico, tono Controller. Cifras y riesgo arriba, evidencia abajo. Honesto sobre datos parciales. Idioma del usuario.',
    '["refinement__query_dataset","refinement__preview_transform","refinement__get_schema","refinement__describe_silver","mcp-infra__search_rag","mcp-infra__request_admin_help"]'::jsonb,
    '{"cartridges":["sap_s4hana"],"kinds":["document","schema"]}'::jsonb,
    'claude-sonnet-4-6', 2000, 0.3,
    '{"variables":{},"triggers":["cartera","vencidas","balance contable","saldo cuentas","gasto proveedores","cobranza"]}'::jsonb
)
ON CONFLICT DO NOTHING;
