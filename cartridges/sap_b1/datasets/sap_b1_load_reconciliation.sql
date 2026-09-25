-- sap_b1_load_reconciliation  (gold)  cartridge: sap_b1
-- sources: ["silver/sap_b1/sap_b1_source_counts", "silver/sap_b1/sap_b1_cinf_latest", "silver/sap_b1/sap_b1_oadm_latest", "silver/sap_b1/sap_b1_ocrn_latest", "silver/sap_b1/sap_b1_ortt_latest", "silver/sap_b1/sap_b1_oact_latest", "silver/sap_b1/sap_b1_ofpr_latest", "silver/sap_b1/sap_b1_oprc_latest", "silver/sap_b1/sap_b1_ocrg_latest", "silver/sap_b1/sap_b1_oslp_latest", "silver/sap_b1/sap_b1_owhs_latest", "silver/sap_b1/sap_b1_oitb_latest", "silver/sap_b1/sap_b1_oitw_latest", "silver/sap_b1/sap_b1_obtn_latest", "silver/sap_b1/sap_b1_obtq_latest", "silver/sap_b1/sap_b1_oibt_latest", "silver/sap_b1/sap_b1_ospp_latest", "silver/sap_b1/sap_b1_osri_latest", "silver/sap_b1/sap_b1_sri1_latest", "silver/sap_b1/sap_b1_ocrd_latest", "silver/sap_b1/sap_b1_oitm_latest", "silver/sap_b1/sap_b1_oitt_latest", "silver/sap_b1/sap_b1_itt1_latest", "silver/sap_b1/sap_b1_oinv_latest", "silver/sap_b1/sap_b1_inv1_latest", "silver/sap_b1/sap_b1_orin_latest", "silver/sap_b1/sap_b1_rin1_latest", "silver/sap_b1/sap_b1_odln_latest", "silver/sap_b1/sap_b1_dln1_latest", "silver/sap_b1/sap_b1_ordn_latest", "silver/sap_b1/sap_b1_rdn1_latest", "silver/sap_b1/sap_b1_ordr_latest", "silver/sap_b1/sap_b1_rdr1_latest", "silver/sap_b1/sap_b1_opch_latest", "silver/sap_b1/sap_b1_pch1_latest", "silver/sap_b1/sap_b1_orpc_latest", "silver/sap_b1/sap_b1_rpc1_latest", "silver/sap_b1/sap_b1_opdn_latest", "silver/sap_b1/sap_b1_pdn1_latest", "silver/sap_b1/sap_b1_opor_latest", "silver/sap_b1/sap_b1_por1_latest", "silver/sap_b1/sap_b1_ojdt_latest", "silver/sap_b1/sap_b1_jdt1_latest", "silver/sap_b1/sap_b1_owtr_latest", "silver/sap_b1/sap_b1_wtr1_latest", "silver/sap_b1/sap_b1_owor_latest", "silver/sap_b1/sap_b1_wor1_latest", "silver/sap_b1/sap_b1_oinm_latest", "silver/sap_b1/sap_b1_ibt1_latest"]
-- description: What arrived against what Business One holds, per company and table: the newest row count the agent took at the source (inside the 24-month window for dated tables, the whole table otherwise) against the rows the platform keeps in silver for the same window, the share loaded and a status (ok when they match, faltan when the platform has fewer rows, sobran when it has more, sin_conteo when the count failed), with the table's business name and extraction mode. This is the progress of the initial load and the evidence behind the signed mapping.

WITH counts AS (
    SELECT * FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_source_counts/**/*.parquet')
),
catalog AS (
    SELECT * FROM (VALUES
        ('CINF', 'Información de la empresa', 'full', FALSE),
        ('OADM', 'Parámetros de la empresa', 'full', FALSE),
        ('OCRN', 'Monedas', 'full', FALSE),
        ('ORTT', 'Tipos de cambio', 'full', FALSE),
        ('OACT', 'Catálogo de cuentas', 'full', FALSE),
        ('OFPR', 'Periodos contables', 'full', FALSE),
        ('OPRC', 'Centros de costo', 'full', FALSE),
        ('OCRG', 'Grupos de socios de negocio', 'full', FALSE),
        ('OSLP', 'Vendedores', 'full', FALSE),
        ('OWHS', 'Almacenes', 'full', FALSE),
        ('OITB', 'Grupos de artículos', 'full', FALSE),
        ('OITW', 'Existencias por almacén', 'full', FALSE),
        ('OBTN', 'Lotes', 'full', FALSE),
        ('OBTQ', 'Cantidades por lote', 'full', FALSE),
        ('OIBT', 'Cantidades por lote (tabla anterior)', 'full', FALSE),
        ('OSPP', 'Precios especiales', 'full', FALSE),
        ('OSRI', 'Números de serie', 'full', FALSE),
        ('SRI1', 'Movimientos de números de serie', 'full', FALSE),
        ('OCRD', 'Socios de negocio (clientes y proveedores)', 'incremental', FALSE),
        ('OITM', 'Artículos', 'incremental', FALSE),
        ('OITT', 'Listas de materiales', 'incremental', FALSE),
        ('ITT1', 'Componentes de listas de materiales', 'incremental', FALSE),
        ('OINV', 'Facturas de clientes', 'incremental', TRUE),
        ('INV1', 'Líneas de facturas de clientes', 'incremental', TRUE),
        ('ORIN', 'Notas de crédito de clientes', 'incremental', TRUE),
        ('RIN1', 'Líneas de notas de crédito de clientes', 'incremental', TRUE),
        ('ODLN', 'Entregas', 'incremental', TRUE),
        ('DLN1', 'Líneas de entregas', 'incremental', TRUE),
        ('ORDN', 'Devoluciones', 'incremental', TRUE),
        ('RDN1', 'Líneas de devoluciones', 'incremental', TRUE),
        ('ORDR', 'Pedidos de clientes', 'incremental', TRUE),
        ('RDR1', 'Líneas de pedidos de clientes', 'incremental', TRUE),
        ('OPCH', 'Facturas de proveedores', 'incremental', TRUE),
        ('PCH1', 'Líneas de facturas de proveedores', 'incremental', TRUE),
        ('ORPC', 'Notas de crédito de proveedores', 'incremental', TRUE),
        ('RPC1', 'Líneas de notas de crédito de proveedores', 'incremental', TRUE),
        ('OPDN', 'Entradas de mercancía', 'incremental', TRUE),
        ('PDN1', 'Líneas de entradas de mercancía', 'incremental', TRUE),
        ('OPOR', 'Órdenes de compra', 'incremental', TRUE),
        ('POR1', 'Líneas de órdenes de compra', 'incremental', TRUE),
        ('OJDT', 'Asientos contables', 'incremental', TRUE),
        ('JDT1', 'Líneas de asientos contables', 'incremental', TRUE),
        ('OWTR', 'Traslados entre almacenes', 'incremental', TRUE),
        ('WTR1', 'Líneas de traslados entre almacenes', 'incremental', TRUE),
        ('OWOR', 'Órdenes de producción', 'incremental', TRUE),
        ('WOR1', 'Componentes de órdenes de producción', 'incremental', TRUE),
        ('OINM', 'Movimientos de inventario', 'incremental', TRUE),
        ('IBT1', 'Movimientos de lotes', 'incremental', TRUE)
    ) t(entity, business_name, mode, dated)
),
platform AS (
    SELECT 'CINF' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_cinf_latest/**/*.parquet') s
    GROUP BY 1, 2
    UNION ALL
    SELECT 'OADM' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_oadm_latest/**/*.parquet') s
    GROUP BY 1, 2
    UNION ALL
    SELECT 'OCRN' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ocrn_latest/**/*.parquet') s
    GROUP BY 1, 2
    UNION ALL
    SELECT 'ORTT' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ortt_latest/**/*.parquet') s
    GROUP BY 1, 2
    UNION ALL
    SELECT 'OACT' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_oact_latest/**/*.parquet') s
    GROUP BY 1, 2
    UNION ALL
    SELECT 'OFPR' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ofpr_latest/**/*.parquet') s
    GROUP BY 1, 2
    UNION ALL
    SELECT 'OPRC' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_oprc_latest/**/*.parquet') s
    GROUP BY 1, 2
    UNION ALL
    SELECT 'OCRG' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ocrg_latest/**/*.parquet') s
    GROUP BY 1, 2
    UNION ALL
    SELECT 'OSLP' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_oslp_latest/**/*.parquet') s
    GROUP BY 1, 2
    UNION ALL
    SELECT 'OWHS' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_owhs_latest/**/*.parquet') s
    GROUP BY 1, 2
    UNION ALL
    SELECT 'OITB' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_oitb_latest/**/*.parquet') s
    GROUP BY 1, 2
    UNION ALL
    SELECT 'OITW' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_oitw_latest/**/*.parquet') s
    GROUP BY 1, 2
    UNION ALL
    SELECT 'OBTN' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_obtn_latest/**/*.parquet') s
    GROUP BY 1, 2
    UNION ALL
    SELECT 'OBTQ' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_obtq_latest/**/*.parquet') s
    GROUP BY 1, 2
    UNION ALL
    SELECT 'OIBT' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_oibt_latest/**/*.parquet') s
    GROUP BY 1, 2
    UNION ALL
    SELECT 'OSPP' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ospp_latest/**/*.parquet') s
    GROUP BY 1, 2
    UNION ALL
    SELECT 'OSRI' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_osri_latest/**/*.parquet') s
    GROUP BY 1, 2
    UNION ALL
    SELECT 'SRI1' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_sri1_latest/**/*.parquet') s
    GROUP BY 1, 2
    UNION ALL
    SELECT 'OCRD' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ocrd_latest/**/*.parquet') s
    GROUP BY 1, 2
    UNION ALL
    SELECT 'OITM' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_oitm_latest/**/*.parquet') s
    GROUP BY 1, 2
    UNION ALL
    SELECT 'OITT' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_oitt_latest/**/*.parquet') s
    GROUP BY 1, 2
    UNION ALL
    SELECT 'ITT1' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_itt1_latest/**/*.parquet') s
    GROUP BY 1, 2
    UNION ALL
    SELECT 'OINV' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_oinv_latest/**/*.parquet') s
    JOIN counts c ON c.company = s.company AND c.entity = 'OINV'
    WHERE CAST(s.doc_date AS DATE) >= CAST(c.window_start AS DATE) AND CAST(s.doc_date AS DATE) < CAST(c.window_end AS DATE)
    GROUP BY 1, 2
    UNION ALL
    SELECT 'INV1' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_inv1_latest/**/*.parquet') s
    JOIN read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_oinv_latest/**/*.parquet') h ON h.company = s.company AND h.doc_entry = s.doc_entry
    JOIN counts c ON c.company = s.company AND c.entity = 'INV1'
    WHERE CAST(h.doc_date AS DATE) >= CAST(c.window_start AS DATE) AND CAST(h.doc_date AS DATE) < CAST(c.window_end AS DATE)
    GROUP BY 1, 2
    UNION ALL
    SELECT 'ORIN' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_orin_latest/**/*.parquet') s
    JOIN counts c ON c.company = s.company AND c.entity = 'ORIN'
    WHERE CAST(s.doc_date AS DATE) >= CAST(c.window_start AS DATE) AND CAST(s.doc_date AS DATE) < CAST(c.window_end AS DATE)
    GROUP BY 1, 2
    UNION ALL
    SELECT 'RIN1' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_rin1_latest/**/*.parquet') s
    JOIN read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_orin_latest/**/*.parquet') h ON h.company = s.company AND h.doc_entry = s.doc_entry
    JOIN counts c ON c.company = s.company AND c.entity = 'RIN1'
    WHERE CAST(h.doc_date AS DATE) >= CAST(c.window_start AS DATE) AND CAST(h.doc_date AS DATE) < CAST(c.window_end AS DATE)
    GROUP BY 1, 2
    UNION ALL
    SELECT 'ODLN' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_odln_latest/**/*.parquet') s
    JOIN counts c ON c.company = s.company AND c.entity = 'ODLN'
    WHERE CAST(s.doc_date AS DATE) >= CAST(c.window_start AS DATE) AND CAST(s.doc_date AS DATE) < CAST(c.window_end AS DATE)
    GROUP BY 1, 2
    UNION ALL
    SELECT 'DLN1' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_dln1_latest/**/*.parquet') s
    JOIN read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_odln_latest/**/*.parquet') h ON h.company = s.company AND h.doc_entry = s.doc_entry
    JOIN counts c ON c.company = s.company AND c.entity = 'DLN1'
    WHERE CAST(h.doc_date AS DATE) >= CAST(c.window_start AS DATE) AND CAST(h.doc_date AS DATE) < CAST(c.window_end AS DATE)
    GROUP BY 1, 2
    UNION ALL
    SELECT 'ORDN' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ordn_latest/**/*.parquet') s
    JOIN counts c ON c.company = s.company AND c.entity = 'ORDN'
    WHERE CAST(s.doc_date AS DATE) >= CAST(c.window_start AS DATE) AND CAST(s.doc_date AS DATE) < CAST(c.window_end AS DATE)
    GROUP BY 1, 2
    UNION ALL
    SELECT 'RDN1' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_rdn1_latest/**/*.parquet') s
    JOIN read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ordn_latest/**/*.parquet') h ON h.company = s.company AND h.doc_entry = s.doc_entry
    JOIN counts c ON c.company = s.company AND c.entity = 'RDN1'
    WHERE CAST(h.doc_date AS DATE) >= CAST(c.window_start AS DATE) AND CAST(h.doc_date AS DATE) < CAST(c.window_end AS DATE)
    GROUP BY 1, 2
    UNION ALL
    SELECT 'ORDR' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ordr_latest/**/*.parquet') s
    JOIN counts c ON c.company = s.company AND c.entity = 'ORDR'
    WHERE CAST(s.doc_date AS DATE) >= CAST(c.window_start AS DATE) AND CAST(s.doc_date AS DATE) < CAST(c.window_end AS DATE)
    GROUP BY 1, 2
    UNION ALL
    SELECT 'RDR1' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_rdr1_latest/**/*.parquet') s
    JOIN read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ordr_latest/**/*.parquet') h ON h.company = s.company AND h.doc_entry = s.doc_entry
    JOIN counts c ON c.company = s.company AND c.entity = 'RDR1'
    WHERE CAST(h.doc_date AS DATE) >= CAST(c.window_start AS DATE) AND CAST(h.doc_date AS DATE) < CAST(c.window_end AS DATE)
    GROUP BY 1, 2
    UNION ALL
    SELECT 'OPCH' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_opch_latest/**/*.parquet') s
    JOIN counts c ON c.company = s.company AND c.entity = 'OPCH'
    WHERE CAST(s.doc_date AS DATE) >= CAST(c.window_start AS DATE) AND CAST(s.doc_date AS DATE) < CAST(c.window_end AS DATE)
    GROUP BY 1, 2
    UNION ALL
    SELECT 'PCH1' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_pch1_latest/**/*.parquet') s
    JOIN read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_opch_latest/**/*.parquet') h ON h.company = s.company AND h.doc_entry = s.doc_entry
    JOIN counts c ON c.company = s.company AND c.entity = 'PCH1'
    WHERE CAST(h.doc_date AS DATE) >= CAST(c.window_start AS DATE) AND CAST(h.doc_date AS DATE) < CAST(c.window_end AS DATE)
    GROUP BY 1, 2
    UNION ALL
    SELECT 'ORPC' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_orpc_latest/**/*.parquet') s
    JOIN counts c ON c.company = s.company AND c.entity = 'ORPC'
    WHERE CAST(s.doc_date AS DATE) >= CAST(c.window_start AS DATE) AND CAST(s.doc_date AS DATE) < CAST(c.window_end AS DATE)
    GROUP BY 1, 2
    UNION ALL
    SELECT 'RPC1' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_rpc1_latest/**/*.parquet') s
    JOIN read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_orpc_latest/**/*.parquet') h ON h.company = s.company AND h.doc_entry = s.doc_entry
    JOIN counts c ON c.company = s.company AND c.entity = 'RPC1'
    WHERE CAST(h.doc_date AS DATE) >= CAST(c.window_start AS DATE) AND CAST(h.doc_date AS DATE) < CAST(c.window_end AS DATE)
    GROUP BY 1, 2
    UNION ALL
    SELECT 'OPDN' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_opdn_latest/**/*.parquet') s
    JOIN counts c ON c.company = s.company AND c.entity = 'OPDN'
    WHERE CAST(s.doc_date AS DATE) >= CAST(c.window_start AS DATE) AND CAST(s.doc_date AS DATE) < CAST(c.window_end AS DATE)
    GROUP BY 1, 2
    UNION ALL
    SELECT 'PDN1' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_pdn1_latest/**/*.parquet') s
    JOIN read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_opdn_latest/**/*.parquet') h ON h.company = s.company AND h.doc_entry = s.doc_entry
    JOIN counts c ON c.company = s.company AND c.entity = 'PDN1'
    WHERE CAST(h.doc_date AS DATE) >= CAST(c.window_start AS DATE) AND CAST(h.doc_date AS DATE) < CAST(c.window_end AS DATE)
    GROUP BY 1, 2
    UNION ALL
    SELECT 'OPOR' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_opor_latest/**/*.parquet') s
    JOIN counts c ON c.company = s.company AND c.entity = 'OPOR'
    WHERE CAST(s.doc_date AS DATE) >= CAST(c.window_start AS DATE) AND CAST(s.doc_date AS DATE) < CAST(c.window_end AS DATE)
    GROUP BY 1, 2
    UNION ALL
    SELECT 'POR1' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_por1_latest/**/*.parquet') s
    JOIN read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_opor_latest/**/*.parquet') h ON h.company = s.company AND h.doc_entry = s.doc_entry
    JOIN counts c ON c.company = s.company AND c.entity = 'POR1'
    WHERE CAST(h.doc_date AS DATE) >= CAST(c.window_start AS DATE) AND CAST(h.doc_date AS DATE) < CAST(c.window_end AS DATE)
    GROUP BY 1, 2
    UNION ALL
    SELECT 'OJDT' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ojdt_latest/**/*.parquet') s
    JOIN counts c ON c.company = s.company AND c.entity = 'OJDT'
    WHERE CAST(s.ref_date AS DATE) >= CAST(c.window_start AS DATE) AND CAST(s.ref_date AS DATE) < CAST(c.window_end AS DATE)
    GROUP BY 1, 2
    UNION ALL
    SELECT 'JDT1' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_jdt1_latest/**/*.parquet') s
    JOIN read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ojdt_latest/**/*.parquet') h ON h.company = s.company AND h.trans_id = s.trans_id
    JOIN counts c ON c.company = s.company AND c.entity = 'JDT1'
    WHERE CAST(h.ref_date AS DATE) >= CAST(c.window_start AS DATE) AND CAST(h.ref_date AS DATE) < CAST(c.window_end AS DATE)
    GROUP BY 1, 2
    UNION ALL
    SELECT 'OWTR' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_owtr_latest/**/*.parquet') s
    JOIN counts c ON c.company = s.company AND c.entity = 'OWTR'
    WHERE CAST(s.doc_date AS DATE) >= CAST(c.window_start AS DATE) AND CAST(s.doc_date AS DATE) < CAST(c.window_end AS DATE)
    GROUP BY 1, 2
    UNION ALL
    SELECT 'WTR1' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_wtr1_latest/**/*.parquet') s
    JOIN read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_owtr_latest/**/*.parquet') h ON h.company = s.company AND h.doc_entry = s.doc_entry
    JOIN counts c ON c.company = s.company AND c.entity = 'WTR1'
    WHERE CAST(h.doc_date AS DATE) >= CAST(c.window_start AS DATE) AND CAST(h.doc_date AS DATE) < CAST(c.window_end AS DATE)
    GROUP BY 1, 2
    UNION ALL
    SELECT 'OWOR' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_owor_latest/**/*.parquet') s
    JOIN counts c ON c.company = s.company AND c.entity = 'OWOR'
    WHERE CAST(s.post_date AS DATE) >= CAST(c.window_start AS DATE) AND CAST(s.post_date AS DATE) < CAST(c.window_end AS DATE)
    GROUP BY 1, 2
    UNION ALL
    SELECT 'WOR1' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_wor1_latest/**/*.parquet') s
    JOIN read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_owor_latest/**/*.parquet') h ON h.company = s.company AND h.doc_entry = s.doc_entry
    JOIN counts c ON c.company = s.company AND c.entity = 'WOR1'
    WHERE CAST(h.post_date AS DATE) >= CAST(c.window_start AS DATE) AND CAST(h.post_date AS DATE) < CAST(c.window_end AS DATE)
    GROUP BY 1, 2
    UNION ALL
    SELECT 'OINM' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_oinm_latest/**/*.parquet') s
    JOIN counts c ON c.company = s.company AND c.entity = 'OINM'
    WHERE CAST(s.doc_date AS DATE) >= CAST(c.window_start AS DATE) AND CAST(s.doc_date AS DATE) < CAST(c.window_end AS DATE)
    GROUP BY 1, 2
    UNION ALL
    SELECT 'IBT1' AS entity, s.company, COUNT(*) AS platform_rows
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ibt1_latest/**/*.parquet') s
    JOIN counts c ON c.company = s.company AND c.entity = 'IBT1'
    WHERE CAST(s.doc_date AS DATE) >= CAST(c.window_start AS DATE) AND CAST(s.doc_date AS DATE) < CAST(c.window_end AS DATE)
    GROUP BY 1, 2
)
SELECT
    c.company,
    c.entity,
    k.business_name,
    k.mode,
    k.dated,
    c.window_start,
    c.window_end,
    c.counted_at,
    c.source_rows,
    COALESCE(p.platform_rows, 0)                                            AS platform_rows,
    COALESCE(p.platform_rows, 0) - c.source_rows                            AS difference,
    CASE WHEN c.source_rows > 0 THEN ROUND(100.0 * LEAST(COALESCE(p.platform_rows, 0), c.source_rows) / c.source_rows, 2)
         WHEN c.error IS NULL THEN 100 END                                   AS loaded_pct,
    CASE WHEN c.error IS NOT NULL THEN 'sin_conteo'
         WHEN COALESCE(p.platform_rows, 0) = c.source_rows THEN 'ok'
         WHEN COALESCE(p.platform_rows, 0) < c.source_rows THEN 'faltan'
         ELSE 'sobran' END                                                  AS status,
    c.error
FROM counts c
LEFT JOIN catalog k ON k.entity = c.entity
LEFT JOIN platform p ON p.company = c.company AND p.entity = c.entity
ORDER BY c.company, c.entity
