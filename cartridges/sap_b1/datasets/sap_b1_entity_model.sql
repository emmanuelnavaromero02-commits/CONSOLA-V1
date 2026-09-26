-- sap_b1_entity_model  (gold)  cartridge: sap_b1
-- sources: ["silver/sap_b1/sap_b1_customer_crosswalk", "silver/sap_b1/sap_b1_supplier_crosswalk", "silver/sap_b1/sap_b1_item_crosswalk", "silver/sap_b1/sap_b1_business_partners", "silver/sap_b1/sap_b1_items", "silver/sap_b1/sap_b1_oslp_latest", "silver/sap_b1/sap_b1_ocrg_latest", "silver/sap_b1/sap_b1_obtn_latest", "silver/sap_b1/sap_b1_obtq_latest", "silver/sap_b1/sap_b1_itt1_latest", "silver/sap_b1/sap_b1_ar_invoice_lines", "silver/sap_b1/sap_b1_ap_invoice_lines"]
-- description: The unified business model of the group per entity (cliente, producto, canal, distribuidora, materia_prima, proveedor, vendedor, lote) and company, plus the group as a whole: records in Business One, identities once the three companies are unified (customers and suppliers by tax id, items by barcode, channels and sales employees by normalized name, batches by item and batch number, distributors by the intercompany mapping), identities shared by more than one company, records with every relation complete and the rule that defines complete, and orphans (references in documents or stock to a master record that does not exist).

WITH partners AS (
    SELECT * FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_business_partners/**/*.parquet')
),
items AS (
    SELECT * FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_items/**/*.parquet')
),
sellers AS (
    SELECT * FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_oslp_latest/**/*.parquet')
),
groups AS (
    SELECT * FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ocrg_latest/**/*.parquet')
),
sales AS (
    SELECT company, card_code, item_code, slp_code, is_intercompany, counterparty_company
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ar_invoice_lines/**/*.parquet')
    WHERE canceled = 'N'
),
supplier_docs AS (
    SELECT company, card_code
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ap_invoice_lines/**/*.parquet')
    WHERE canceled = 'N'
),
components AS (
    SELECT DISTINCT company, code AS item_code
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_itt1_latest/**/*.parquet')
    WHERE code IS NOT NULL
),
members AS (
    SELECT 'cliente' AS entity, x.company, x.customer_key AS identity,
           x.rfc_valid AND NOT x.rfc_generic AND p.group_code IS NOT NULL AND COALESCE(p.slp_code, -1) > 0 AS complete
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_customer_crosswalk/**/*.parquet') x
    LEFT JOIN partners p ON p.company = x.company AND p.card_code = x.card_code
    UNION ALL
    SELECT 'proveedor', company, supplier_key, rfc_valid AND NOT rfc_generic
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_supplier_crosswalk/**/*.parquet')
    UNION ALL
    SELECT 'producto', x.company, x.item_key, i.item_group_code IS NOT NULL AND i.default_warehouse IS NOT NULL
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_item_crosswalk/**/*.parquet') x
    LEFT JOIN items i ON i.company = x.company AND i.item_code = x.item_code
    UNION ALL
    SELECT 'materia_prima', c.company, COALESCE(x.item_key, 'CODE:' || c.item_code),
           i.preferred_supplier IS NOT NULL AND i.lead_time_days IS NOT NULL
    FROM components c
    LEFT JOIN items i ON i.company = c.company AND i.item_code = c.item_code
    LEFT JOIN read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_item_crosswalk/**/*.parquet') x
      ON x.company = c.company AND x.item_code = c.item_code
    UNION ALL
    SELECT 'canal', g.company, 'CANAL:' || upper(trim(strip_accents(g.group_name))),
           EXISTS (SELECT 1 FROM partners p WHERE p.company = g.company AND p.group_code = g.group_code)
    FROM groups g
    WHERE g.group_type = 'C'
    UNION ALL
    SELECT 'vendedor', s.company, 'VENDEDOR:' || upper(trim(strip_accents(s.slp_name))),
           COALESCE(s.active, 'Y') = 'Y'
           AND (EXISTS (SELECT 1 FROM partners p WHERE p.company = s.company AND p.slp_code = s.slp_code)
                OR EXISTS (SELECT 1 FROM sales l WHERE l.company = s.company AND l.slp_code = s.slp_code))
    FROM sellers s
    WHERE s.slp_code > 0
    UNION ALL
    SELECT 'distribuidora', d.company, 'EMPRESA:' || d.company,
           EXISTS (SELECT 1 FROM sales l WHERE l.company = d.company AND NOT l.is_intercompany)
    FROM (SELECT DISTINCT counterparty_company AS company FROM sales WHERE is_intercompany AND counterparty_company IS NOT NULL) d
    UNION ALL
    SELECT 'lote', n.company, COALESCE(x.item_key, 'CODE:' || n.item_code) || '|' || upper(n.dist_number),
           n.exp_date IS NOT NULL AND i.item_code IS NOT NULL
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_obtn_latest/**/*.parquet') n
    LEFT JOIN items i ON i.company = n.company AND i.item_code = n.item_code
    LEFT JOIN read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_item_crosswalk/**/*.parquet') x
      ON x.company = n.company AND x.item_code = n.item_code
),
orphans AS (
    SELECT 'cliente' AS entity, s.company, COUNT(DISTINCT s.card_code) AS orphans
    FROM sales s WHERE NOT EXISTS (SELECT 1 FROM partners p WHERE p.company = s.company AND p.card_code = s.card_code)
    GROUP BY 1, 2
    UNION ALL
    SELECT 'proveedor', b.company, COUNT(DISTINCT b.card_code)
    FROM supplier_docs b WHERE NOT EXISTS (SELECT 1 FROM partners p WHERE p.company = b.company AND p.card_code = b.card_code)
    GROUP BY 1, 2
    UNION ALL
    SELECT 'producto', s.company, COUNT(DISTINCT s.item_code)
    FROM sales s WHERE s.item_code IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM items i WHERE i.company = s.company AND i.item_code = s.item_code)
    GROUP BY 1, 2
    UNION ALL
    SELECT 'materia_prima', c.company, COUNT(*)
    FROM components c WHERE NOT EXISTS (SELECT 1 FROM items i WHERE i.company = c.company AND i.item_code = c.item_code)
    GROUP BY 1, 2
    UNION ALL
    SELECT 'vendedor', s.company, COUNT(DISTINCT s.slp_code)
    FROM sales s WHERE COALESCE(s.slp_code, -1) > 0
      AND NOT EXISTS (SELECT 1 FROM sellers o WHERE o.company = s.company AND o.slp_code = s.slp_code)
    GROUP BY 1, 2
    UNION ALL
    SELECT 'canal', p.company, COUNT(DISTINCT p.group_code)
    FROM partners p WHERE p.card_type = 'C' AND p.group_code IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM groups g WHERE g.company = p.company AND g.group_code = p.group_code)
    GROUP BY 1, 2
    UNION ALL
    SELECT 'lote', q.company, COUNT(*)
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_obtq_latest/**/*.parquet') q
    WHERE NOT EXISTS (
        SELECT 1 FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_obtn_latest/**/*.parquet') n
        WHERE n.company = q.company AND n.item_code = q.item_code AND n.sys_number = q.sys_number)
    GROUP BY 1, 2
),
shared AS (
    SELECT entity, identity, COUNT(DISTINCT company) AS companies
    FROM members
    GROUP BY 1, 2
),
per_company AS (
    SELECT m.entity, m.company,
           COUNT(*)                                                        AS records,
           COUNT(DISTINCT m.identity)                                      AS identities,
           COUNT(DISTINCT m.identity) FILTER (WHERE s.companies > 1)       AS shared_identities,
           COUNT(*) FILTER (WHERE m.complete)                              AS complete_records
    FROM members m
    JOIN shared s ON s.entity = m.entity AND s.identity = m.identity
    GROUP BY 1, 2
    UNION ALL
    SELECT m.entity, 'grupo',
           COUNT(*),
           COUNT(DISTINCT m.identity),
           COUNT(DISTINCT m.identity) FILTER (WHERE s.companies > 1),
           COUNT(*) FILTER (WHERE m.complete)
    FROM members m
    JOIN shared s ON s.entity = m.entity AND s.identity = m.identity
    GROUP BY 1
),
rules AS (
    SELECT * FROM (VALUES
        ('cliente',       'RFC válido y no genérico, grupo (canal) y vendedor asignados'),
        ('proveedor',     'RFC válido y no genérico'),
        ('producto',      'Grupo de artículo y almacén por defecto asignados'),
        ('materia_prima', 'Proveedor preferido y tiempo de entrega asignados'),
        ('canal',         'Al menos un cliente en el grupo'),
        ('vendedor',      'Activo y con clientes o ventas'),
        ('distribuidora', 'Empresa del grupo que compra al grupo y vende a clientes externos'),
        ('lote',          'Fecha de caducidad y artículo existente')
    ) t(entity, relation_rule)
)
SELECT
    p.entity,
    p.company,
    p.records,
    p.identities,
    p.shared_identities,
    p.complete_records,
    ROUND(100.0 * p.complete_records / NULLIF(p.records, 0), 2)            AS completeness_pct,
    COALESCE(o.orphans, CASE WHEN p.company = 'grupo'
                             THEN (SELECT SUM(x.orphans) FROM orphans x WHERE x.entity = p.entity) END, 0) AS orphans,
    r.relation_rule
FROM per_company p
LEFT JOIN orphans o ON o.entity = p.entity AND o.company = p.company
LEFT JOIN rules r ON r.entity = p.entity
ORDER BY p.entity, p.company = 'grupo', p.company
