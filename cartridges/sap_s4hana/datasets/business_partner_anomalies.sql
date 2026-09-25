-- business_partner_anomalies  (gold)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/BusinessPartner", "raw/sap_s4hana/Customer", "raw/sap_s4hana/Supplier", "raw/sap_s4hana/BusinessPartnerAddress", "silver/sap_s4hana/sap_s4hana_business_partner_full"]
-- description: Detección automática de irregularidades en Business Partners (preparación Fase 3). UNION de varios casos con tipo, severidad y detalle.

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
