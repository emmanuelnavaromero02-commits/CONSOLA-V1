# SAP HCM — Hints para el asistente

Cuando un usuario pregunta en el contexto del cartucho SAP HCM, el asistente
sigue estas reglas además de las globales.

## 1. Identidad del cartucho

- **Cartucho:** SAP HCM (Human Capital Management) — gestión de personal en SAP ERP clásico.
- **Idioma de los datos:** los campos vienen en alemán técnico SAP (`Pernr`, `Begda`,
  `Endda`, `Orgeh`, `Kostl`, `Plans`, `Massn`, `Awart`...). Responde siempre en español
  de negocio y traduce el campo técnico a su significado.
- **Cobertura:** 10 entidades extraídas vía OData (Employee Central + Organizational
  Management + Time): `EmployeeMaster`, `PersonalData`, `ContractData`, `OrgUnit`,
  `Position`, `CostCenter`, `JobCode`, `EmployeeActions`, `LeaveAbsence`, `WorkSchedule`.
- **Modelo de 3 capas:** raw (bronze 1:1 con OData) → silver (snapshot vigente, snake_case)
  → gold (agregados y KPIs en `pggold.gold_*`).

## 2. Modelo de datos esencial

- **Espina:** `EmployeeMaster` (infotipo PA0001) es la entidad central; cada empleado se
  identifica por `Pernr`.
- **Vigencia:** `Begda` (inicio) y `Endda` (fin) delimitan la validez de cada registro.
  Un registro está vigente cuando `Begda <= hoy <= Endda`.
- **Organización:** empleado → posición (`Plans`) → unidad organizacional (`Orgeh`) →
  centro de costo (`Kostl`).
- **Tiempo:** ausencias en `LeaveAbsence` (PA2001; `Awart`=tipo, `Abwtg`=días hábiles);
  horarios en `WorkSchedule` (PA0007).
- **Acciones de personal:** altas, bajas y cambios en `EmployeeActions` (`Massn`=tipo de medida).
- **Golds disponibles (`pggold.gold_*`):** `gold_headcount_by_department`,
  `gold_headcount_by_costcenter`, `gold_headcount_by_position_type`,
  `gold_absence_by_type_and_month`, `gold_absence_balance_by_employee`,
  `gold_employees_anomalies`, `gold_manager_hierarchy`, `gold_workforce_cost_monthly`.

## 3. Convenciones

- **Empleado activo** = registro de `EmployeeMaster` vigente (`Endda >= CURRENT_DATE` y
  `Begda <= CURRENT_DATE`) sin baja en `EmployeeActions` (`Massn` distinto de baja).
- **Mes** = mes calendario. No hay año fiscal partido (a diferencia de Replicon).
- **`Pernr` está shadowed** por privacidad: en las respuestas trabaja solo con agregados;
  nunca muestres el `Pernr` crudo de un empleado individual.
- Nombres de columna: raw conserva los nombres técnicos SAP; silver/gold están en
  snake_case de negocio (`org_name`, `cost_center`, `employee_group`, `total_days_workable`).

## 4. Reglas operativas

1. **Conteos de plantilla** → usa `gold_headcount_by_department`, `gold_headcount_by_costcenter`
   y `gold_headcount_by_position_type`. El copiloto los resuelve con los KBs
   `kb_sap_hcm_headcount_active_by_department`, `kb_sap_hcm_headcount_by_costcenter` y
   `kb_sap_hcm_workforce_composition_by_position_type`.
2. **Ausencias** → usa `gold_absence_by_type_and_month` (tendencia) y
   `gold_absence_balance_by_employee` (top por empleado). KBs:
   `kb_sap_hcm_absence_trend_monthly`, `kb_sap_hcm_absence_top_employees`.
3. **Detección de problemas** → usa `gold_employees_anomalies`. KB:
   `kb_sap_hcm_employees_anomalies_active`.
4. **Span of control / jerarquía** → usa `gold_manager_hierarchy`. KB:
   `kb_sap_hcm_manager_span_of_control`.
5. Antes de inventar SQL, confirma columnas reales con `search_rag` o `get_schema`. Al leer
   `raw/sap_hcm/*` con `read_parquet`, incluye siempre `hive_partitioning=true, union_by_name=true`.
6. Si la pregunta no la cubre ningún gold, baja al silver correspondiente. Si el silver
   tampoco la cubre, explica la limitación y sugiere una alternativa; escala al admin con
   `request_admin_help`.

## 5. Apps publicadas

- `sap_hcm_headcount_dashboard` — visualización ejecutiva de plantilla activa por
  departamento, centro de costo y tipo de posición.
- `sap_hcm_people_quality_dashboard` — auditoría operativa: anomalías de datos, tendencia
  de ausencias y span of control.

## 6. Limitaciones honestas

- **No se extrae HRP1001** (relaciones de Organizational Management) → `gold_manager_hierarchy`
  viene plana: `manager_pernr` nulo y `direct_reports`=0 hasta que se extraiga esa fuente
  (HRP1001 / Sbrtr).
- **No se extrae PA0008** (BasicPay) → `gold_workforce_cost_monthly` queda con el costo
  pendiente (TODO) hasta contar con esa fuente.
- Los datos de compensación son sensibles → se manejan cifrados; no los expongas a nivel
  de empleado individual.
