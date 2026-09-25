# SAP SuccessFactors — Hints para el asistente

Cuando un usuario pregunta en el contexto del cartucho SAP SuccessFactors, el
asistente sigue estas reglas además de las globales.

## 1. Identidad del cartucho

- **Cartucho:** SAP SuccessFactors — HXM cloud (Human Experience Management).
- **Dominio:** 4 módulos HXM — Employee Central, Recruiting, Performance y Learning.
- **Idioma de los datos:** los campos vienen en inglés técnico SAP (`User`,
  `EmpEmployment`, `EmpJob`, `Candidate`, `JobRequisition`...). Responde siempre en
  español de negocio y traduce el término técnico a su significado.
- **Cobertura:** entidades extraídas vía OData v2 (Employee Central + Foundation Objects
  + Recruiting + Performance + Learning).
- **Modelo de 3 capas:** raw (bronze 1:1 con OData) → silver (snapshot vigente, snake_case)
  → gold (agregados y KPIs en `pggold.gold_*`).

## 2. Modelo de datos esencial

- **Empleado núcleo:** `User` → `PerPerson` → `PerPersonal` → `EmpEmployment` → `EmpJob`.
- **Foundation Objects:** `FOCompany`, `FODepartment`, `FODivision`, `FOLocation`,
  `FOBusinessUnit`, `FOCostCenter`, `FOJobCode`, `Position`.
- **Compensación:** `EmpCompensation` + `EmpPayCompRecurring` + `EmpPayCompNonRecurring`.
- **Reclutamiento:** `Candidate`, `JobRequisition`.
- **Performance:** `GoalPlan`, `PerformanceReview`.
- **Learning:** `LearningItem`.
- **Shadowing asimétrico (importante):** `User.userId` está shadowed (hash), pero el
  `userId` de la familia `Emp*` está en claro. Para JOINs cross-entidad, las familias
  `Emp*` + `PerPersonal` unen por claves planas; `User`/`PerPerson` quedan fuera del 360
  por ese hashing asimétrico — no cruces el hash de `User` con el `userId` plano.
- **Golds disponibles (`pggold.gold_*`):** `gold_sap_successfactors_headcount_by_department`,
  `..._headcount_by_location`, `..._headcount_by_company`, `..._compensation_distribution`,
  `..._recruitment_funnel`, `..._turnover_by_period`, `..._manager_hierarchy`,
  `..._employees_anomalies`.
- **Golds Talento / WisdomBit:** `gold_sap_successfactors_talent_employee_profile`,
  `..._talent_role_profile`, `..._talent_mobility_history`, `..._talent_readiness`,
  `..._talent_9box`, `..._talent_signals`.

## 3. Convenciones

- **Empleado activo** = `EmpEmployment` con `endDate` nulo o `endDate >= hoy`.
- **Manager** = `EmpJob.managerId` (poblado y en claro en SF; la jerarquía es real).
- **Mes / año fiscal** = mes y año calendario.
- **Campos cifrados** (`payCompValue`, `dateOfBirth`, `nationalId`): NO se agregan nunca;
  no calcules distribución salarial ni uses datos personales sensibles a nivel individuo.

## 4. Reglas operativas

1. **Headcount** → usa `gold_sap_successfactors_headcount_by_department`,
   `..._headcount_by_location`, `..._headcount_by_company`. KBs:
   `kb_sap_successfactors_headcount_by_department`, `kb_sap_successfactors_headcount_by_location`,
   `kb_sap_successfactors_headcount_by_company`.
2. **Rotación** → usa `gold_sap_successfactors_turnover_by_period`. KB:
   `kb_sap_successfactors_turnover_recent`.
3. **Reclutamiento** → usa `gold_sap_successfactors_recruitment_funnel` (parcial). KB:
   `kb_sap_successfactors_recruitment_funnel`.
4. **Org chart / span** → usa `gold_sap_successfactors_manager_hierarchy` (real,
   `direct_reports` poblado). KB: `kb_sap_successfactors_manager_hierarchy_depth`.
5. **Anomalías de datos** → usa `gold_sap_successfactors_employees_anomalies`. KB:
   `kb_sap_successfactors_employees_anomalies`.
6. **Composición por tipo de empleo** → KB `kb_sap_successfactors_workforce_distribution`
   (lee del silver `sap_successfactors_empemployment_latest`, porque ningún gold expone
   `employee_class`).
7. **Talento / WisdomBit** → usa los golds `talent_*`. `KB-EMPLEADOS` y movilidad salen
   de `employee_360`, jerarquía, `EmpJob` y `FOJobCode`; `KB-ROLES` es parcial; Fit Score,
   readiness y 9-box deben responder `insufficient_data` hasta tener desempeño,
   competencias y aspiración. KBs: `kb_sap_successfactors_talent_employee_profile`,
   `kb_sap_successfactors_talent_role_profile`, `kb_sap_successfactors_talent_mobility_history`,
   `kb_sap_successfactors_talent_readiness`, `kb_sap_successfactors_talent_9box`,
   `kb_sap_successfactors_talent_signals`.
8. **Distribución de compensación** → NO es posible: `payCompValue` está cifrado. El
   WisdomBit Talento tiene compensación apagada.
9. Antes de inventar SQL, confirma columnas reales con `search_rag` o `get_schema`. Al
   leer `raw/sap_successfactors/*` con `read_parquet`, incluye siempre
   `hive_partitioning=true, union_by_name=true`. Si la pregunta no la cubre ningún gold,
   baja al silver; si tampoco, explica la limitación y escala con `request_admin_help`.

## 5. Apps publicadas

- `sap_successfactors_workforce_overview` — vista HR Director: headcount por departamento,
  ubicación y compañía, y rotación mensual.
- `sap_successfactors_talent_health` — salud de talento: anomalías de datos, embudo de
  reclutamiento y span of control.

## 6. Limitaciones honestas

- **`payCompValue` cifrado** → la distribución salarial / compensación no es calculable;
  el gold `compensation_distribution` queda limitado por ese cifrado.
- **`recruitment_funnel` es metadata-gated**: las etapas por candidato usan
  `JobApplication` cuando el tenant la expone; si falta, queda parcial a nivel
  de requisición.
- **`turnover_by_period`** se basa en `EmpEmploymentTermination`; si no hay bajas
  en la ventana extraída, el resultado será vacío.
- **Composición por tipo de empleo** se lee del silver (no hay gold dedicado).
- El entityset de Learning (`LearningItem`) usa el default y no está verificado contra un
  entorno externo.
- **Talento C/P/A**: desempeño (`PerformanceReview`/`FormHeader`), objetivos (`GoalPlan`),
  competencias/skills y aspiración dependen de `$metadata` real del tenant. Si faltan, no
  inventes scores: reporta `insufficient_data` y los blockers.
- **WisdomBit Talento** usa perfil `retail`, pesos C/P/A 0.45/0.30/0.25 y señales
  de recomendación solamente; no hace write-back a SuccessFactors.
