-- ─────────────────────────────────────────────────────────────────────────────
-- MODecissions Cartridge: SAP Business One — seed configuration
-- GENERATED from app/config/entities.yaml (cartridges/sap_b1/tests keep both
-- in step). Run once to register this cartridge in a new installation.
-- Safe to re-run: every insert is ON CONFLICT DO NOTHING / DO UPDATE.
-- Company schema names are NOT here: they are runtime configuration.
-- ─────────────────────────────────────────────────────────────────────────────

-- ── Cartridge header ──────────────────────────────────────────────────────────
INSERT INTO cartridges (id, name, version, description, pattern, category, bronze_path)
VALUES (
    'sap_b1',
    'SAP Business One',
    '0.1.0',
    'SAP Business One 10 (HANA), multi-company: maestros, documentos de venta y compra, asientos, inventario, lotes y producción.',
    'dag-based',
    'cartridge',
    'raw/sap_b1/{entity}/load_date={date}/'
)
ON CONFLICT (id) DO UPDATE
    SET name        = EXCLUDED.name,
        version     = EXCLUDED.version,
        description = EXCLUDED.description,
        updated_at  = NOW();

-- ── DAGs ──────────────────────────────────────────────────────────────────────
INSERT INTO cartridge_dags (cartridge_id, dag_id, file, description, trigger, params)
VALUES
    ('sap_b1', 'sap_b1_extract',     'sap_b1_extract.py',     'Extrae una tabla de Business One en Bronze (full, incremental o histórico), todas las empresas configuradas', 'on-demand', '["entity","mode","from_date","to_date"]'),
    ('sap_b1', 'sap_b1_extract_all', 'sap_b1_extract_all.py', 'Extrae todas las tablas habilitadas',                                                                        'on-demand', '["mode","entities"]')
ON CONFLICT (cartridge_id, dag_id) DO NOTHING;

-- ── Entities ──────────────────────────────────────────────────────────────────
INSERT INTO entity_config
    (cartridge_id, entity, display_name, description, mode,
     watermark_field, watermark_format, page_size, primary_key, date_field,
     dag_id, enabled, trigger_type)
VALUES
    ('sap_b1', 'CINF', 'Company info (CINF)', 'Company database version and name (one row per company).', 'full', NULL, NULL, 100, NULL, NULL, 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'OADM', 'Company settings (OADM)', 'Company code, local currency (MainCurncy) and system currency (SysCurrncy).', 'full', NULL, NULL, 100, 'Code', NULL, 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'OCRN', 'Currencies (OCRN)', 'Currency codes configured in the company.', 'full', NULL, NULL, 500, 'CurrCode', NULL, 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'ORTT', 'Exchange rates (ORTT)', 'Daily exchange rates per currency (RateDate, Currency).', 'full', NULL, NULL, 5000, 'RateDate,Currency', NULL, 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'OACT', 'Chart of accounts (OACT)', 'G/L accounts: ActType N (P&L), I (income), E (expense); Postable flag.', 'full', NULL, NULL, 5000, 'AcctCode', NULL, 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'OFPR', 'Posting periods (OFPR)', 'Financial posting periods with their date ranges.', 'full', NULL, NULL, 1000, 'AbsEntry', NULL, 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'OPRC', 'Cost centres (OPRC)', 'Profit centres / cost centres (OcrCode dimension).', 'full', NULL, NULL, 1000, 'PrcCode', NULL, 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'OCRG', 'Business partner groups (OCRG)', 'Customer and supplier groups.', 'full', NULL, NULL, 1000, 'GroupCode', NULL, 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'OSLP', 'Sales employees (OSLP)', 'Sales employees / buyers referenced by SlpCode.', 'full', NULL, NULL, 1000, 'SlpCode', NULL, 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'OWHS', 'Warehouses (OWHS)', 'Warehouse codes and names.', 'full', NULL, NULL, 1000, 'WhsCode', NULL, 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'OITB', 'Item groups (OITB)', 'Item groups.', 'full', NULL, NULL, 1000, 'ItmsGrpCod', NULL, 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'OITW', 'Stock by warehouse (OITW)', 'On-hand, committed and on-order quantities per item and warehouse (snapshot).', 'full', NULL, NULL, 5000, 'ItemCode,WhsCode', NULL, 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'OBTN', 'Batches (OBTN)', 'Batch master (DistNumber, manufacture and expiry dates).', 'full', NULL, NULL, 5000, 'AbsEntry', NULL, 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'OBTQ', 'Batch quantities (OBTQ)', 'Batch quantity per item, batch (SysNumber) and warehouse (snapshot).', 'full', NULL, NULL, 5000, 'ItemCode,SysNumber,WhsCode', NULL, 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'OIBT', 'Batch quantities (legacy) (OIBT)', 'Batch quantity per item, BatchNum and warehouse (compatibility table).', 'full', NULL, NULL, 5000, 'ItemCode,BatchNum,WhsCode', NULL, 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'OCRD', 'Business partners (OCRD)', 'Customers (CardType C) and suppliers (CardType S) with their currency and group.', 'incremental', 'UpdateDate', 'b1_update_ts', 2000, 'CardCode', NULL, 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'OITM', 'Items (OITM)', 'Item master: inventory / sales / purchase flags, batch management, default warehouse.', 'incremental', 'UpdateDate', 'b1_update_ts', 2000, 'ItemCode', NULL, 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'OITT', 'Bills of materials (OITT)', 'Bill of materials headers (production BOMs).', 'incremental', 'UpdateDate', 'b1_update_ts', 1000, 'Code', NULL, 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'ITT1', 'Bill of materials lines (ITT1)', 'Components per BOM; read through OITT so an edited BOM brings all its lines.', 'incremental', 'UpdateDate', 'b1_update_ts', 5000, 'Father,ChildNum', NULL, 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'OINV', 'A/R invoices (OINV)', 'A/R invoices headers, ObjType 13: totals in document, local and system currency; CANCELED N/Y/C.', 'incremental', 'UpdateDate', 'b1_update_ts', 2000, 'DocEntry', 'DocDate', 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'INV1', 'A/R invoices lines (INV1)', 'A/R invoices lines, read through OINV: quantities, prices, line totals in three currencies, base/target links.', 'incremental', 'UpdateDate', 'b1_update_ts', 5000, 'DocEntry,LineNum', 'DocDate', 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'ORIN', 'A/R credit memos (ORIN)', 'A/R credit memos headers, ObjType 14: totals in document, local and system currency; CANCELED N/Y/C.', 'incremental', 'UpdateDate', 'b1_update_ts', 2000, 'DocEntry', 'DocDate', 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'RIN1', 'A/R credit memos lines (RIN1)', 'A/R credit memos lines, read through ORIN: quantities, prices, line totals in three currencies, base/target links.', 'incremental', 'UpdateDate', 'b1_update_ts', 5000, 'DocEntry,LineNum', 'DocDate', 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'ODLN', 'Deliveries (ODLN)', 'Deliveries headers, ObjType 15: totals in document, local and system currency; CANCELED N/Y/C.', 'incremental', 'UpdateDate', 'b1_update_ts', 2000, 'DocEntry', 'DocDate', 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'DLN1', 'Deliveries lines (DLN1)', 'Deliveries lines, read through ODLN: quantities, prices, line totals in three currencies, base/target links.', 'incremental', 'UpdateDate', 'b1_update_ts', 5000, 'DocEntry,LineNum', 'DocDate', 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'ORDN', 'Returns (ORDN)', 'Returns headers, ObjType 16: totals in document, local and system currency; CANCELED N/Y/C.', 'incremental', 'UpdateDate', 'b1_update_ts', 2000, 'DocEntry', 'DocDate', 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'RDN1', 'Returns lines (RDN1)', 'Returns lines, read through ORDN: quantities, prices, line totals in three currencies, base/target links.', 'incremental', 'UpdateDate', 'b1_update_ts', 5000, 'DocEntry,LineNum', 'DocDate', 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'ORDR', 'Sales orders (ORDR)', 'Sales orders headers, ObjType 17: totals in document, local and system currency; CANCELED N/Y/C.', 'incremental', 'UpdateDate', 'b1_update_ts', 2000, 'DocEntry', 'DocDate', 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'RDR1', 'Sales orders lines (RDR1)', 'Sales orders lines, read through ORDR: quantities, prices, line totals in three currencies, base/target links.', 'incremental', 'UpdateDate', 'b1_update_ts', 5000, 'DocEntry,LineNum', 'DocDate', 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'OPCH', 'A/P invoices (OPCH)', 'A/P invoices headers, ObjType 18: totals in document, local and system currency; CANCELED N/Y/C.', 'incremental', 'UpdateDate', 'b1_update_ts', 2000, 'DocEntry', 'DocDate', 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'PCH1', 'A/P invoices lines (PCH1)', 'A/P invoices lines, read through OPCH: quantities, prices, line totals in three currencies, base/target links.', 'incremental', 'UpdateDate', 'b1_update_ts', 5000, 'DocEntry,LineNum', 'DocDate', 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'ORPC', 'A/P credit memos (ORPC)', 'A/P credit memos headers, ObjType 19: totals in document, local and system currency; CANCELED N/Y/C.', 'incremental', 'UpdateDate', 'b1_update_ts', 2000, 'DocEntry', 'DocDate', 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'RPC1', 'A/P credit memos lines (RPC1)', 'A/P credit memos lines, read through ORPC: quantities, prices, line totals in three currencies, base/target links.', 'incremental', 'UpdateDate', 'b1_update_ts', 5000, 'DocEntry,LineNum', 'DocDate', 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'OPDN', 'Goods receipt POs (OPDN)', 'Goods receipt POs headers, ObjType 20: totals in document, local and system currency; CANCELED N/Y/C.', 'incremental', 'UpdateDate', 'b1_update_ts', 2000, 'DocEntry', 'DocDate', 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'PDN1', 'Goods receipt POs lines (PDN1)', 'Goods receipt POs lines, read through OPDN: quantities, prices, line totals in three currencies, base/target links.', 'incremental', 'UpdateDate', 'b1_update_ts', 5000, 'DocEntry,LineNum', 'DocDate', 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'OPOR', 'Purchase orders (OPOR)', 'Purchase orders headers, ObjType 22: totals in document, local and system currency; CANCELED N/Y/C.', 'incremental', 'UpdateDate', 'b1_update_ts', 2000, 'DocEntry', 'DocDate', 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'POR1', 'Purchase orders lines (POR1)', 'Purchase orders lines, read through OPOR: quantities, prices, line totals in three currencies, base/target links.', 'incremental', 'UpdateDate', 'b1_update_ts', 5000, 'DocEntry,LineNum', 'DocDate', 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'OJDT', 'Journal entries (OJDT)', 'Journal entry headers: RefDate, TransType (originating object), StornoToTr for reversals.', 'incremental', 'UpdateDate', 'b1_update_ts', 2000, 'TransId', 'RefDate', 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'JDT1', 'Journal entry lines (JDT1)', 'Journal lines in local, foreign and system currency; ShortName carries the CardCode on control-account lines.', 'incremental', 'UpdateDate', 'b1_update_ts', 5000, 'TransId,Line_ID', 'RefDate', 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'OWTR', 'Inventory transfers (OWTR)', 'Inventory transfer headers, ObjType 67: source warehouse (Filler) and target warehouse (ToWhsCode); stock moves, money does not.', 'incremental', 'UpdateDate', 'b1_update_ts', 2000, 'DocEntry', 'DocDate', 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'WTR1', 'Inventory transfer lines (WTR1)', 'Inventory transfer lines, read through OWTR: item, quantity, from and to warehouse.', 'incremental', 'UpdateDate', 'b1_update_ts', 5000, 'DocEntry,LineNum', 'DocDate', 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'OWOR', 'Production orders (OWOR)', 'Production order headers: planned, completed and rejected quantities, status and dates.', 'incremental', 'UpdateDate', 'b1_update_ts', 2000, 'DocEntry', 'PostDate', 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'WOR1', 'Production order components (WOR1)', 'Components per production order, read through OWOR.', 'incremental', 'UpdateDate', 'b1_update_ts', 5000, 'DocEntry,LineNum', 'PostDate', 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'OINM', 'Inventory movements (OINM)', 'Inventory transaction log (a view over OIVL/IVL1 in B1 >= 8.8); rows never change. One TransNum per document with one row per line (TransSeq): TransNum is the watermark, (TransNum, TransSeq) the page key.', 'incremental', 'TransNum', 'integer', 5000, 'TransNum,TransSeq', 'DocDate', 'sap_b1_extract', TRUE, 'manual'),
    ('sap_b1', 'IBT1', 'Batch transactions (IBT1)', 'Batch quantities per document line, Direction 0 in / 1 out; rows never change, LogEntry (identity, validate in HANA) is the watermark and page key.', 'incremental', 'LogEntry', 'integer', 5000, 'LogEntry', 'DocDate', 'sap_b1_extract', TRUE, 'manual')
ON CONFLICT (cartridge_id, entity) DO NOTHING;
