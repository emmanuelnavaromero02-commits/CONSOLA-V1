-- manager_hierarchy  (gold)  cartridge: sap_hcm
-- sources: ["raw/sap_hcm/EmployeeMaster", "raw/sap_hcm/PersonalData", "raw/sap_hcm/ContractData"]
-- description: Árbol de supervisión por empleado. El vínculo manager no está disponible en el bronze actual (PA0001 extraído no incluye Sbrtr y no se extrae HRP1001); se entrega cada empleado activo con manager NULL hasta habilitar esa fuente.

-- TODO: poblar manager_pernr cuando exista la fuente del jefe:
--   (a) HRP1001Set relación A002 (posición -> posición padre) -> manager por posición, o
--   (b) PA0001.Sbrtr (clave de supervisor) si se añade a select_fields.
-- Con esa arista, sustituir por un WITH RECURSIVE para profundidad y conteo de reportes.
WITH emp AS (
    SELECT pernr, full_name, org_unit_id
    FROM read_parquet('s3://{bucket}/silver/sap_hcm/sap_hcm_employee_master_full/**/*.parquet')
    WHERE is_active = TRUE
)
SELECT
    pernr                            AS pernr,           -- shadowed (FK)
    full_name                        AS full_name,       -- masked
    org_unit_id                      AS org_unit_id,
    CAST(NULL AS VARCHAR)            AS manager_pernr,
    CAST(NULL AS VARCHAR)            AS manager_name,
    0                                AS direct_reports,
    0                                AS depth
FROM emp
ORDER BY pernr
