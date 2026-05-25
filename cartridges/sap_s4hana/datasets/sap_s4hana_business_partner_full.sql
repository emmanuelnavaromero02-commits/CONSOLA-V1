-- sap_s4hana_business_partner_full  (silver)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/BusinessPartner", "raw/sap_s4hana/Customer", "raw/sap_s4hana/Supplier", "raw/sap_s4hana/BusinessPartnerAddress"]
-- description: Vista 360 del Business Partner con sus roles (cliente / proveedor) y dirección principal. Una fila por partner.

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
