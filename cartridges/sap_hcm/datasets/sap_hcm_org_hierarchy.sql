-- sap_hcm_org_hierarchy  (silver)  cartridge: sap_hcm
-- sources: ["raw/sap_hcm/OrgUnit"]
-- description: Estructura organizacional. Lista las unidades org con columnas de jerarquía. El padre queda NULL hasta habilitar HRP1001 (relaciones OM), que no se extrae hoy.

-- TODO: la relación padre-hijo de OM vive en HRP1001Set (no extraído en Bloque A).
-- Cuando se habilite raw/sap_hcm/OrgRelationship (Subty A002), reemplazar este
-- bloque por un CTE recursivo: edges(child, parent) -> WITH RECURSIVE walk(...)
-- para poblar parent_id / depth / path reales.
WITH org AS (
    SELECT
        org_unit_id,
        org_unit_name,
        ROW_NUMBER() OVER (PARTITION BY org_unit_id ORDER BY valid_from DESC) AS rn
    FROM read_parquet('s3://{bucket}/silver/sap_hcm/sap_hcm_orgunit_latest/**/*.parquet')
)
SELECT
    org_unit_id                       AS org_id,
    org_unit_name                     AS org_name,
    CAST(NULL AS VARCHAR)             AS parent_id,
    CAST(NULL AS VARCHAR)             AS parent_name,
    0                                 AS depth,
    org_unit_name                     AS path
FROM org
WHERE rn = 1
ORDER BY org_id
