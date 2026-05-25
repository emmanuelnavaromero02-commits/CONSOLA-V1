-- 81_sap_s4hana_datasets_seed.sql
--
-- Seeds the SAP S/4HANA silver/gold datasets into the `datasets` catalog table.
-- Mirrors infra/init/80_sap_hcm_datasets_seed.sql (Block B, HCM): the per-dataset
-- SQL bodies live in cartridges/sap_s4hana/datasets/*.sql (source of truth) and
-- are inlined here as dollar-quoted literals for refinement to materialize.
--
-- workspace_id (NOT NULL since migration 23) is set to the first workspace, the
-- same target migration 23 backfills; this migration runs after 23 so it must be
-- provided explicitly (the lesson from the HCM datasets PR).
--
-- 19 silver + 8 gold = 27 datasets. Idempotent: ON CONFLICT (name) DO NOTHING.
-- Registered via this infra migration (not the cartridge seed.sql) because the
-- cartridge seed guard disallows INSERT INTO datasets / INSERT ... SELECT.

INSERT INTO datasets (name, layer, cartridge, sources, sql_def, description, column_mapping, schedule, updated_at, workspace_id)
VALUES
($seed$sap_s4hana_businesspartner_latest$seed$, $seed$silver$seed$, $seed$sap_s4hana$seed$, $seed$["raw/sap_s4hana/BusinessPartner"]$seed$::jsonb, $seed$
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_s4hana/BusinessPartner/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_s4hana/BusinessPartner/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    BusinessPartner               AS business_partner,        -- shadowed en bronze (FK estable)
    BusinessPartnerCategory       AS business_partner_category,
    BusinessPartnerFullName       AS full_name,               -- masked en bronze
    BusinessPartnerName           AS name,                    -- masked en bronze
    LegalForm                     AS legal_form,
    CAST(CreationDate AS DATE)    AS creation_date,
    CAST(LastChangeDate AS DATE)  AS last_change_date,
    load_date
FROM latest
ORDER BY business_partner
$seed$, $seed$Última extracción de Business Partners (maestro) con campos tipados. El id y el nombre llegan ya protegidos desde bronze (shadowed / masked).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_s4hana_customer_latest$seed$, $seed$silver$seed$, $seed$sap_s4hana$seed$, $seed$["raw/sap_s4hana/Customer"]$seed$::jsonb, $seed$
-- NOTA: A_Customer no declara select_fields; se tipan los campos de cabecera
-- estándar. Los datos fiscales/bancarios (TaxNumber*, IBAN…) viven en
-- sub-entidades y no se exponen aquí.
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_s4hana/Customer/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_s4hana/Customer/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    Customer                      AS customer,                -- shadowed en bronze (FK estable)
    CustomerName                  AS customer_name,
    CustomerFullName              AS customer_full_name,
    CustomerAccountGroup          AS account_group,
    CAST(LastChangeDate AS DATE)  AS last_change_date,
    load_date
FROM latest
ORDER BY customer
$seed$, $seed$Última extracción del maestro de clientes (A_Customer). El id de cliente llega shadowed desde bronze.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_s4hana_supplier_latest$seed$, $seed$silver$seed$, $seed$sap_s4hana$seed$, $seed$["raw/sap_s4hana/Supplier"]$seed$::jsonb, $seed$
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_s4hana/Supplier/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_s4hana/Supplier/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    Supplier                      AS supplier,                -- shadowed en bronze (FK estable)
    SupplierName                  AS supplier_name,
    SupplierFullName              AS supplier_full_name,
    SupplierAccountGroup          AS account_group,
    CAST(LastChangeDate AS DATE)  AS last_change_date,
    load_date
FROM latest
ORDER BY supplier
$seed$, $seed$Última extracción del maestro de proveedores (A_Supplier). El id de proveedor llega shadowed desde bronze.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_s4hana_businesspartneraddress_latest$seed$, $seed$silver$seed$, $seed$sap_s4hana$seed$, $seed$["raw/sap_s4hana/BusinessPartnerAddress"]$seed$::jsonb, $seed$
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_s4hana/BusinessPartnerAddress/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_s4hana/BusinessPartnerAddress/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    BusinessPartner               AS business_partner,        -- shadowed en bronze (FK)
    AddressID                     AS address_id,
    StreetName                    AS street_name,             -- masked en bronze
    HouseNumber                   AS house_number,            -- masked en bronze
    PostalCode                    AS postal_code,
    CityName                      AS city_name,
    Country                       AS country,
    Region                        AS region,
    load_date
FROM latest
ORDER BY business_partner, address_id
$seed$, $seed$Última extracción de direcciones de Business Partner. Calle/número llegan masked; localidad/país en claro.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_s4hana_product_latest$seed$, $seed$silver$seed$, $seed$sap_s4hana$seed$, $seed$["raw/sap_s4hana/Product"]$seed$::jsonb, $seed$
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_s4hana/Product/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_s4hana/Product/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    Product                          AS product,
    ProductType                      AS product_type,
    ProductGroup                     AS product_group,
    BaseUnit                         AS base_unit,
    CAST(CreationDate AS DATE)       AS creation_date,
    CAST(LastChangeDateTime AS TIMESTAMP) AS last_change,
    load_date
FROM latest
ORDER BY product
$seed$, $seed$Última extracción del maestro de productos / materiales (A_Product).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_s4hana_companycode_latest$seed$, $seed$silver$seed$, $seed$sap_s4hana$seed$, $seed$["raw/sap_s4hana/CompanyCode"]$seed$::jsonb, $seed$
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_s4hana/CompanyCode/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_s4hana/CompanyCode/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    CompanyCode          AS company_code,
    CompanyCodeName      AS company_code_name,
    Country              AS country,
    Currency             AS currency,
    load_date
FROM latest
ORDER BY company_code
$seed$, $seed$Última extracción del maestro de sociedades (A_CompanyCode).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_s4hana_costcenter_latest$seed$, $seed$silver$seed$, $seed$sap_s4hana$seed$, $seed$["raw/sap_s4hana/CostCenter"]$seed$::jsonb, $seed$
-- NOTA: el nombre del centro de costo vive en A_CostCenterText (no extraído);
-- aquí se tipan los campos de cabecera de A_CostCenter.
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_s4hana/CostCenter/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_s4hana/CostCenter/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    CostCenter                       AS cost_center,
    CompanyCode                      AS company_code,
    ControllingArea                  AS controlling_area,
    CostCenterCurrency               AS currency,
    CAST(ValidityStartDate AS DATE)  AS valid_from,
    CAST(ValidityEndDate AS DATE)    AS valid_to,
    load_date
FROM latest
ORDER BY cost_center
$seed$, $seed$Última extracción del maestro de centros de costo (A_CostCenter).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_s4hana_glaccount_latest$seed$, $seed$silver$seed$, $seed$sap_s4hana$seed$, $seed$["raw/sap_s4hana/GLAccount"]$seed$::jsonb, $seed$
-- NOTA: nombres de campo estándar del chart of accounts; ajustar al tenant si difiere.
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_s4hana/GLAccount/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_s4hana/GLAccount/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    GLAccount            AS gl_account,
    GLAccountType        AS gl_account_type,
    GLAccountGroup       AS gl_account_group,
    load_date
FROM latest
ORDER BY gl_account
$seed$, $seed$Última extracción del plan de cuentas (A_GLAccountInChartOfAccounts).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_s4hana_salesorder_latest$seed$, $seed$silver$seed$, $seed$sap_s4hana$seed$, $seed$["raw/sap_s4hana/SalesOrder"]$seed$::jsonb, $seed$
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_s4hana/SalesOrder/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_s4hana/SalesOrder/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    SalesOrder                          AS sales_order,
    SalesOrderType                      AS sales_order_type,
    SoldToParty                         AS sold_to_party,
    SalesOrganization                   AS sales_organization,
    CAST(SalesOrderDate AS DATE)        AS sales_order_date,
    OverallSDProcessStatus              AS overall_status,
    CAST(TotalNetAmount AS DECIMAL(15,2)) AS total_net_amount,
    TransactionCurrency                 AS currency,
    CAST(LastChangeDateTime AS TIMESTAMP) AS last_change,
    load_date
FROM latest
ORDER BY sales_order
$seed$, $seed$Última extracción de cabeceras de pedido de venta (A_SalesOrder). SoldToParty es el código de cliente (en claro: A_SalesOrder no tiene regla de protección).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_s4hana_salesorderitem_latest$seed$, $seed$silver$seed$, $seed$sap_s4hana$seed$, $seed$["raw/sap_s4hana/SalesOrderItem"]$seed$::jsonb, $seed$
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_s4hana/SalesOrderItem/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_s4hana/SalesOrderItem/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    SalesOrder                          AS sales_order,
    SalesOrderItem                      AS sales_order_item,
    Material                            AS material,
    CAST(RequestedQuantity AS DECIMAL(15,3)) AS requested_quantity,
    CAST(NetAmount AS DECIMAL(15,2))    AS net_amount,
    Plant                               AS plant,
    load_date
FROM latest
ORDER BY sales_order, sales_order_item
$seed$, $seed$Última extracción de líneas de pedido de venta (A_SalesOrderItem).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_s4hana_billingdocument_latest$seed$, $seed$silver$seed$, $seed$sap_s4hana$seed$, $seed$["raw/sap_s4hana/BillingDocument"]$seed$::jsonb, $seed$
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_s4hana/BillingDocument/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_s4hana/BillingDocument/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    BillingDocument                     AS billing_document,
    CAST(BillingDocumentDate AS DATE)   AS billing_document_date,
    SoldToParty                         AS sold_to_party,
    CAST(TotalNetAmount AS DECIMAL(15,2)) AS total_net_amount,
    TransactionCurrency                 AS currency,
    PaymentTerms                        AS payment_terms,
    CAST(LastChangeDateTime AS TIMESTAMP) AS last_change,
    load_date
FROM latest
ORDER BY billing_document
$seed$, $seed$Última extracción de cabeceras de factura de venta (A_BillingDocument). SoldToParty es el código de cliente en claro.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_s4hana_billingdocumentitem_latest$seed$, $seed$silver$seed$, $seed$sap_s4hana$seed$, $seed$["raw/sap_s4hana/BillingDocumentItem"]$seed$::jsonb, $seed$
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_s4hana/BillingDocumentItem/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_s4hana/BillingDocumentItem/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    BillingDocument                     AS billing_document,
    BillingDocumentItem                 AS billing_document_item,
    Material                            AS material,
    CAST(BillingQuantity AS DECIMAL(15,3)) AS billing_quantity,
    CAST(NetAmount AS DECIMAL(15,2))    AS net_amount,
    load_date
FROM latest
ORDER BY billing_document, billing_document_item
$seed$, $seed$Última extracción de líneas de factura de venta (A_BillingDocumentItem).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_s4hana_purchaseorder_latest$seed$, $seed$silver$seed$, $seed$sap_s4hana$seed$, $seed$["raw/sap_s4hana/PurchaseOrder"]$seed$::jsonb, $seed$
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_s4hana/PurchaseOrder/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_s4hana/PurchaseOrder/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    PurchaseOrder                       AS purchase_order,
    PurchaseOrderType                   AS purchase_order_type,
    Supplier                            AS supplier,
    CompanyCode                         AS company_code,
    CAST(PurchaseOrderDate AS DATE)     AS purchase_order_date,
    DocumentCurrency                    AS currency,
    CAST(LastChangeDateTime AS TIMESTAMP) AS last_change,
    load_date
FROM latest
ORDER BY purchase_order
$seed$, $seed$Última extracción de cabeceras de orden de compra (A_PurchaseOrder). Supplier es el código de proveedor en claro.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_s4hana_purchaseorderitem_latest$seed$, $seed$silver$seed$, $seed$sap_s4hana$seed$, $seed$["raw/sap_s4hana/PurchaseOrderItem"]$seed$::jsonb, $seed$
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_s4hana/PurchaseOrderItem/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_s4hana/PurchaseOrderItem/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    PurchaseOrder                       AS purchase_order,
    PurchaseOrderItem                   AS purchase_order_item,
    Material                            AS material,
    CAST(OrderQuantity AS DECIMAL(15,3)) AS order_quantity,
    CAST(NetPriceAmount AS DECIMAL(15,2)) AS net_price_amount,
    Plant                               AS plant,
    load_date
FROM latest
ORDER BY purchase_order, purchase_order_item
$seed$, $seed$Última extracción de líneas de orden de compra (A_PurchaseOrderItem).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_s4hana_supplierinvoice_latest$seed$, $seed$silver$seed$, $seed$sap_s4hana$seed$, $seed$["raw/sap_s4hana/SupplierInvoice"]$seed$::jsonb, $seed$
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_s4hana/SupplierInvoice/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_s4hana/SupplierInvoice/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    SupplierInvoice                     AS supplier_invoice,
    InvoicingParty                      AS invoicing_party,    -- shadowed en bronze (FK)
    CompanyCode                         AS company_code,
    CAST(DocumentDate AS DATE)          AS document_date,
    CAST(InvoiceGrossAmount AS DECIMAL(15,2)) AS invoice_gross_amount,
    DocumentCurrency                    AS currency,
    load_date
FROM latest
ORDER BY supplier_invoice
$seed$, $seed$Última extracción de facturas de proveedor (A_SupplierInvoice). InvoicingParty llega shadowed desde bronze.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_s4hana_sales_orders_full$seed$, $seed$silver$seed$, $seed$sap_s4hana$seed$, $seed$["raw/sap_s4hana/SalesOrder", "raw/sap_s4hana/SalesOrderItem"]$seed$::jsonb, $seed$
-- NOTA de privacidad: no se une al maestro de clientes porque Customer.customer
-- está shadowed (hash) mientras SalesOrder.sold_to_party va en claro (A_SalesOrder
-- no tiene regla de protección); el código de cliente se conserva como dimensión.
WITH orders AS (
    SELECT sales_order, sales_order_type, sold_to_party, sales_organization,
           sales_order_date, overall_status, currency
    FROM read_parquet('s3://{bucket}/silver/sap_s4hana/sap_s4hana_salesorder_latest/**/*.parquet')
),
items AS (
    SELECT sales_order, sales_order_item, material, requested_quantity, net_amount, plant
    FROM read_parquet('s3://{bucket}/silver/sap_s4hana/sap_s4hana_salesorderitem_latest/**/*.parquet')
)
SELECT
    o.sales_order            AS sales_order,
    i.sales_order_item       AS sales_order_item,
    o.sales_order_type       AS sales_order_type,
    o.sold_to_party          AS customer_code,
    o.sales_organization     AS sales_organization,
    o.sales_order_date       AS sales_order_date,
    o.overall_status         AS overall_status,
    i.material               AS material,
    i.requested_quantity     AS requested_quantity,
    i.net_amount             AS net_amount,
    o.currency               AS currency,
    i.plant                  AS plant
FROM orders o
LEFT JOIN items i ON i.sales_order = o.sales_order
ORDER BY o.sales_order, i.sales_order_item
$seed$, $seed$Pedidos de venta a nivel línea, enriquecidos con la cabecera (cliente, fecha, estado, moneda). Una fila por línea.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_s4hana_purchase_orders_full$seed$, $seed$silver$seed$, $seed$sap_s4hana$seed$, $seed$["raw/sap_s4hana/PurchaseOrder", "raw/sap_s4hana/PurchaseOrderItem"]$seed$::jsonb, $seed$
-- NOTA de privacidad: PurchaseOrder.supplier va en claro (A_PurchaseOrder no tiene
-- regla de protección); no se une al maestro de proveedores (Supplier.supplier
-- está shadowed). El código de proveedor se conserva como dimensión.
WITH orders AS (
    SELECT purchase_order, purchase_order_type, supplier, company_code,
           purchase_order_date, currency
    FROM read_parquet('s3://{bucket}/silver/sap_s4hana/sap_s4hana_purchaseorder_latest/**/*.parquet')
),
items AS (
    SELECT purchase_order, purchase_order_item, material, order_quantity, net_price_amount, plant
    FROM read_parquet('s3://{bucket}/silver/sap_s4hana/sap_s4hana_purchaseorderitem_latest/**/*.parquet')
)
SELECT
    o.purchase_order         AS purchase_order,
    i.purchase_order_item    AS purchase_order_item,
    o.purchase_order_type    AS purchase_order_type,
    o.supplier               AS supplier_code,
    o.company_code           AS company_code,
    o.purchase_order_date    AS purchase_order_date,
    i.material               AS material,
    i.order_quantity         AS order_quantity,
    i.net_price_amount       AS net_price_amount,
    ROUND(COALESCE(i.order_quantity, 0) * COALESCE(i.net_price_amount, 0), 2) AS line_value,
    o.currency               AS currency,
    i.plant                  AS plant
FROM orders o
LEFT JOIN items i ON i.purchase_order = o.purchase_order
ORDER BY o.purchase_order, i.purchase_order_item
$seed$, $seed$Órdenes de compra a nivel línea, enriquecidas con la cabecera (proveedor, fecha, sociedad, moneda). Una fila por línea.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_s4hana_invoices_full$seed$, $seed$silver$seed$, $seed$sap_s4hana$seed$, $seed$["raw/sap_s4hana/BillingDocument", "raw/sap_s4hana/BillingDocumentItem"]$seed$::jsonb, $seed$
WITH docs AS (
    SELECT billing_document, billing_document_date, sold_to_party,
           total_net_amount, currency, payment_terms
    FROM read_parquet('s3://{bucket}/silver/sap_s4hana/sap_s4hana_billingdocument_latest/**/*.parquet')
),
items AS (
    SELECT billing_document, billing_document_item, material, billing_quantity, net_amount
    FROM read_parquet('s3://{bucket}/silver/sap_s4hana/sap_s4hana_billingdocumentitem_latest/**/*.parquet')
)
SELECT
    d.billing_document        AS billing_document,
    i.billing_document_item   AS billing_document_item,
    d.billing_document_date   AS billing_document_date,
    d.sold_to_party           AS customer_code,
    i.material                AS material,
    i.billing_quantity        AS billing_quantity,
    i.net_amount              AS net_amount,
    d.total_net_amount        AS document_net_amount,
    d.currency                AS currency,
    d.payment_terms           AS payment_terms
FROM docs d
LEFT JOIN items i ON i.billing_document = d.billing_document
ORDER BY d.billing_document, i.billing_document_item
$seed$, $seed$Facturas de venta a nivel línea, enriquecidas con la cabecera (cliente, fecha, moneda, condiciones de pago). Una fila por línea.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_s4hana_business_partner_full$seed$, $seed$silver$seed$, $seed$sap_s4hana$seed$, $seed$["raw/sap_s4hana/BusinessPartner", "raw/sap_s4hana/Customer", "raw/sap_s4hana/Supplier", "raw/sap_s4hana/BusinessPartnerAddress"]$seed$::jsonb, $seed$
-- Los cuatro maestros comparten el id de partner shadowed con el mismo hash
-- (Customer/Supplier reutilizan el número de BP), por lo que el JOIN por hash es
-- consistente. Nombre y dirección llegan masked desde bronze.
WITH bp AS (
    SELECT business_partner, business_partner_category, full_name, name, legal_form
    FROM read_parquet('s3://{bucket}/silver/sap_s4hana/sap_s4hana_businesspartner_latest/**/*.parquet')
),
cust AS (
    SELECT customer, account_group AS customer_account_group
    FROM read_parquet('s3://{bucket}/silver/sap_s4hana/sap_s4hana_customer_latest/**/*.parquet')
),
supp AS (
    SELECT supplier, account_group AS supplier_account_group
    FROM read_parquet('s3://{bucket}/silver/sap_s4hana/sap_s4hana_supplier_latest/**/*.parquet')
),
addr AS (
    -- Dirección más reciente por partner.
    SELECT business_partner, city_name, country, region,
           ROW_NUMBER() OVER (PARTITION BY business_partner ORDER BY address_id) AS rn
    FROM read_parquet('s3://{bucket}/silver/sap_s4hana/sap_s4hana_businesspartneraddress_latest/**/*.parquet')
)
SELECT
    bp.business_partner                                      AS business_partner,   -- shadowed (FK)
    bp.business_partner_category                             AS category,
    bp.full_name                                             AS full_name,          -- masked
    bp.legal_form                                            AS legal_form,
    CASE WHEN c.customer IS NOT NULL THEN TRUE ELSE FALSE END AS is_customer,
    CASE WHEN s.supplier IS NOT NULL THEN TRUE ELSE FALSE END AS is_supplier,
    c.customer_account_group                                 AS customer_account_group,
    s.supplier_account_group                                 AS supplier_account_group,
    a.city_name                                              AS city_name,
    a.country                                                AS country,
    a.region                                                 AS region
FROM bp
LEFT JOIN cust c ON c.customer = bp.business_partner
LEFT JOIN supp s ON s.supplier = bp.business_partner
LEFT JOIN addr a ON a.business_partner = bp.business_partner AND a.rn = 1
ORDER BY bp.business_partner
$seed$, $seed$Vista 360 del Business Partner con sus roles (cliente / proveedor) y dirección principal. Una fila por partner.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$revenue_by_customer$seed$, $seed$gold$seed$, $seed$sap_s4hana$seed$, $seed$["raw/sap_s4hana/BillingDocument", "raw/sap_s4hana/BillingDocumentItem"]$seed$::jsonb, $seed$
WITH inv AS (
    SELECT customer_code, billing_document, billing_document_date, net_amount, currency
    FROM read_parquet('s3://{bucket}/silver/sap_s4hana/sap_s4hana_invoices_full/**/*.parquet')
    WHERE billing_document_date IS NOT NULL
)
SELECT
    customer_code                                            AS customer_code,
    CAST(DATE_TRUNC('month', billing_document_date) AS DATE) AS revenue_month,
    currency                                                 AS currency,
    ROUND(SUM(net_amount), 2)                                AS revenue,
    COUNT(DISTINCT billing_document)                         AS invoice_count
FROM inv
GROUP BY customer_code, DATE_TRUNC('month', billing_document_date), currency
ORDER BY revenue_month DESC, revenue DESC
$seed$, $seed$Ingresos por cliente y mes a partir de facturas de venta. Ranking de clientes por revenue.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$open_sales_orders$seed$, $seed$gold$seed$, $seed$sap_s4hana$seed$, $seed$["raw/sap_s4hana/SalesOrder", "raw/sap_s4hana/SalesOrderItem"]$seed$::jsonb, $seed$
-- OverallSDProcessStatus = 'C' significa completado; abierto = cualquier otro.
WITH so AS (
    SELECT sales_order, customer_code, sales_order_date, overall_status, net_amount, currency
    FROM read_parquet('s3://{bucket}/silver/sap_s4hana/sap_s4hana_sales_orders_full/**/*.parquet')
    WHERE overall_status IS DISTINCT FROM 'C'
)
SELECT
    customer_code                                                  AS customer_code,
    currency                                                       AS currency,
    COUNT(DISTINCT sales_order)                                    AS open_orders,
    ROUND(SUM(net_amount), 2)                                      AS open_value,
    MIN(sales_order_date)                                          AS oldest_order_date,
    CAST(DATE_DIFF('day', MIN(sales_order_date), CURRENT_DATE) AS INTEGER) AS oldest_age_days
FROM so
GROUP BY customer_code, currency
ORDER BY open_value DESC, customer_code
$seed$, $seed$Backlog de pedidos de venta abiertos (no completados) por cliente: número, valor pendiente y antigüedad del más viejo.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$overdue_billing$seed$, $seed$gold$seed$, $seed$sap_s4hana$seed$, $seed$["raw/sap_s4hana/BillingDocument"]$seed$::jsonb, $seed$
-- TODO: el estado pagado/no pagado vive en partidas abiertas de FI
-- (A_OperationalAcctgDocItem, no extraído). Aquí el vencimiento se estima como
-- billing_document_date + 30 días y se reporta solo el aging por fecha.
WITH bills AS (
    SELECT billing_document, sold_to_party, billing_document_date, total_net_amount, currency
    FROM read_parquet('s3://{bucket}/silver/sap_s4hana/sap_s4hana_billingdocument_latest/**/*.parquet')
    WHERE billing_document_date IS NOT NULL
)
SELECT
    billing_document                                          AS billing_document,
    sold_to_party                                            AS customer_code,
    billing_document_date                                    AS billing_document_date,
    CAST(billing_document_date + INTERVAL 30 DAY AS DATE)    AS estimated_due_date,
    ROUND(total_net_amount, 2)                               AS amount,
    currency                                                 AS currency,
    CAST(DATE_DIFF('day', billing_document_date + INTERVAL 30 DAY, CURRENT_DATE) AS INTEGER) AS days_overdue
FROM bills
WHERE CAST(billing_document_date + INTERVAL 30 DAY AS DATE) < CURRENT_DATE
ORDER BY days_overdue DESC, amount DESC
$seed$, $seed$Cartera vencida (aging) de facturas de venta por antigüedad estimada. El estado de pago real no está disponible; el vencimiento se aproxima por fecha.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$purchase_spend_by_supplier$seed$, $seed$gold$seed$, $seed$sap_s4hana$seed$, $seed$["raw/sap_s4hana/PurchaseOrder", "raw/sap_s4hana/PurchaseOrderItem"]$seed$::jsonb, $seed$
WITH po AS (
    SELECT supplier_code, purchase_order, purchase_order_date, line_value, currency
    FROM read_parquet('s3://{bucket}/silver/sap_s4hana/sap_s4hana_purchase_orders_full/**/*.parquet')
    WHERE purchase_order_date IS NOT NULL
)
SELECT
    supplier_code                                              AS supplier_code,
    CAST(DATE_TRUNC('month', purchase_order_date) AS DATE)     AS spend_month,
    currency                                                   AS currency,
    ROUND(SUM(line_value), 2)                                  AS total_spend,
    COUNT(DISTINCT purchase_order)                             AS po_count
FROM po
GROUP BY supplier_code, DATE_TRUNC('month', purchase_order_date), currency
ORDER BY spend_month DESC, total_spend DESC
$seed$, $seed$Gasto de compras por proveedor y mes (cantidad x precio de línea). Ranking de proveedores por gasto.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$gl_balance_by_account$seed$, $seed$gold$seed$, $seed$sap_s4hana$seed$, $seed$["raw/sap_s4hana/JournalEntryItem"]$seed$::jsonb, $seed$
WITH je AS (
    SELECT
        CompanyCode                              AS company_code,
        GLAccount                                AS gl_account,
        FiscalYear                               AS fiscal_year,
        CAST(AmountInCompanyCodeCurrency AS DECIMAL(17,2)) AS amount
    FROM read_parquet('s3://{bucket}/raw/sap_s4hana/JournalEntryItem/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_s4hana/JournalEntryItem/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    company_code              AS company_code,
    gl_account                AS gl_account,
    fiscal_year               AS fiscal_year,
    ROUND(SUM(amount), 2)     AS balance,
    COUNT(*)                  AS line_items
FROM je
GROUP BY company_code, gl_account, fiscal_year
ORDER BY company_code, gl_account, fiscal_year
$seed$, $seed$Saldo contable por sociedad, cuenta de mayor y ejercicio fiscal, agregado desde las partidas del Universal Journal (ACDOCA).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$inventory_movement_summary$seed$, $seed$gold$seed$, $seed$sap_s4hana$seed$, $seed$["raw/sap_s4hana/MaterialDocumentHeader"]$seed$::jsonb, $seed$
-- TODO: las cantidades por material/clase de movimiento viven en
-- A_MaterialDocumentItem (no extraído). Este resumen es a nivel cabecera:
-- número de documentos de material por mes.
WITH mdh AS (
    SELECT
        MaterialDocument            AS material_document,
        CAST(PostingDate AS DATE)   AS posting_date
    FROM read_parquet('s3://{bucket}/raw/sap_s4hana/MaterialDocumentHeader/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_s4hana/MaterialDocumentHeader/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    CAST(DATE_TRUNC('month', posting_date) AS DATE) AS posting_month,
    COUNT(DISTINCT material_document)               AS movement_documents
FROM mdh
WHERE posting_date IS NOT NULL
GROUP BY DATE_TRUNC('month', posting_date)
ORDER BY posting_month DESC
$seed$, $seed$Resumen mensual de movimientos de inventario (conteo de documentos de material por mes de contabilización).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$cost_center_expense$seed$, $seed$gold$seed$, $seed$sap_s4hana$seed$, $seed$["raw/sap_s4hana/CostCenter", "raw/sap_s4hana/PurchaseOrder"]$seed$::jsonb, $seed$
-- TODO: el gasto real requiere A_PurchaseOrderAccountAssignment (CostCenter por
-- línea de OC), que no está en entities.yaml. Cuando se extraiga, unir por
-- cost_center y SUM(line_value) por mes.
WITH cc AS (
    SELECT cost_center, company_code, controlling_area, currency
    FROM read_parquet('s3://{bucket}/silver/sap_s4hana/sap_s4hana_costcenter_latest/**/*.parquet')
)
SELECT
    cost_center                                     AS cost_center,
    company_code                                    AS company_code,
    controlling_area                                AS controlling_area,
    currency                                        AS currency,
    CAST(NULL AS DECIMAL(15,2))                     AS total_expense,   -- TODO: account assignment
    CAST(DATE_TRUNC('month', CURRENT_DATE) AS DATE) AS snapshot_month
FROM cc
ORDER BY company_code, cost_center
$seed$, $seed$Gasto por centro de costo. PENDIENTE: el enlace compra -> centro de costo vive en la asignación contable de la línea (no extraída); hoy se lista el maestro de centros de costo con expense en NULL.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$business_partner_anomalies$seed$, $seed$gold$seed$, $seed$sap_s4hana$seed$, $seed$["raw/sap_s4hana/BusinessPartner", "raw/sap_s4hana/Customer", "raw/sap_s4hana/Supplier", "raw/sap_s4hana/BusinessPartnerAddress"]$seed$::jsonb, $seed$
-- Mejora propia (estilo employees_anomalies de HCM). Casos cubiertos hoy:
-- partner sin dirección y nombres duplicados. Pendiente (campos no extraídos):
-- customer/supplier sin tax number (sub-entidad fiscal) y partner bloqueado con
-- OCs/SOs activas (flag de bloqueo no extraído).
WITH bp AS (
    SELECT business_partner, full_name, city_name
    FROM read_parquet('s3://{bucket}/silver/sap_s4hana/sap_s4hana_business_partner_full/**/*.parquet')
),
no_address AS (
    SELECT business_partner, full_name,
           'missing_address' AS anomaly_type,
           'medium'          AS severity,
           '{"reason":"business partner sin direccion"}' AS details
    FROM bp
    WHERE city_name IS NULL
),
duplicate_name AS (
    SELECT business_partner, full_name,
           'duplicate_name' AS anomaly_type,
           'low'            AS severity,
           '{"reason":"mismo nombre en multiples business partners"}' AS details
    FROM bp
    WHERE full_name IS NOT NULL
      AND full_name IN (
          SELECT full_name FROM bp WHERE full_name IS NOT NULL
          GROUP BY full_name HAVING COUNT(*) > 1
      )
)
SELECT business_partner, full_name, anomaly_type, severity, details,
       CURRENT_TIMESTAMP AS detected_at
FROM (
    SELECT * FROM no_address
    UNION ALL
    SELECT * FROM duplicate_name
) anomalies
ORDER BY severity, anomaly_type, business_partner
$seed$, $seed$Detección automática de irregularidades en Business Partners (preparación Fase 3). UNION de varios casos con tipo, severidad y detalle.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1))
ON CONFLICT (name) DO NOTHING;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('81_sap_s4hana_datasets_seed.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
