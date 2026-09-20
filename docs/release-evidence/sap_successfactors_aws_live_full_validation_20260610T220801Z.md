# Validacion completa live SAP SuccessFactors AWS

- estado final: `YELLOW`
- fecha/hora UTC: `2026-06-10T22:13:03Z`
- ambiente AWS: `us-east-1` / `<aws-instance-id>`
- console_url: `http://modecissions-public-255609366.us-east-1.elb.amazonaws.com`
- run_id: `SF_LIVE_20260610T220801Z`
- tenant_id: `b95f4d58-c9c8-4fd5-8d07-ddde294c7d78`
- workspace_id: `a2b1ced2-4d92-4bbe-8f9f-9a7cc88bb9f4`
- conn_id: `femsa_sf`
- bucket: `modecissions-lakehouse-783792`

## Resumen

- SuccessFactors real ejecutado: `SI`
- Entidades con datos: `15`
- Entidades vacias: `8`
- Entidades con error: `20`
- Entidades no ejecutadas: `0`

## Pasos

| Paso | Estado | Clasificacion | Evidencia | Nota/Error |
|---|---|---|---|---|
| AWS identity real | PASS | NONE | docs/release-evidence/sap_successfactors_aws_live/SF_LIVE_20260610T220801Z/aws_identity.json |  |
| Public /healthz | PASS | NONE | docs/release-evidence/sap_successfactors_aws_live/SF_LIVE_20260610T220801Z/commands/02-http-healthz.log |  |
| Public /readyz | PASS | NONE | docs/release-evidence/sap_successfactors_aws_live/SF_LIVE_20260610T220801Z/commands/03-http-readyz.log |  |
| Public /readyz?require_data=1 | PASS | NONE | docs/release-evidence/sap_successfactors_aws_live/SF_LIVE_20260610T220801Z/commands/04-http-readyz-require-data-1.log |  |
| AWS services reales | PASS | NONE | docs/release-evidence/sap_successfactors_aws_live/SF_LIVE_20260610T220801Z/commands/05-aws-service-preflight.log |  |
| SuccessFactors extract-all real | PASS | NONE | docs/release-evidence/sap_successfactors_aws_live/SF_LIVE_20260610T220801Z/commands/06-successfactors-extract-all-real.log | SF_LIVE_20260610T220801Z-extract-all |
| Postgres/RDS real | PASS | NONE | docs/release-evidence/sap_successfactors_aws_live/SF_LIVE_20260610T220801Z/commands/07-postgres-rds-registry-real.log |  |
| S3 lakehouse raw/sap_successfactors/ | PASS | NONE | docs/release-evidence/sap_successfactors_aws_live/SF_LIVE_20260610T220801Z/commands/08-s3-list-raw-sap-successfactors.log | sample_count=20 |
| S3 lakehouse silver/sap_successfactors/ | PASS | NONE | docs/release-evidence/sap_successfactors_aws_live/SF_LIVE_20260610T220801Z/commands/09-s3-list-silver-sap-successfactors.log | sample_count=20 |
| S3 lakehouse gold/sap_successfactors/ | PASS | NONE | docs/release-evidence/sap_successfactors_aws_live/SF_LIVE_20260610T220801Z/commands/10-s3-list-gold-sap-successfactors.log | sample_count=20 |
| Gold foundation materialization real | PASS | NONE | docs/release-evidence/sap_successfactors_aws_live/SF_LIVE_20260610T220801Z/commands/11-gold-foundation-materialization-real.log |  |
| Postgres/RDS real | PASS | NONE | docs/release-evidence/sap_successfactors_aws_live/SF_LIVE_20260610T220801Z/commands/12-postgres-rds-registry-real.log |  |
| Gold dataset sap_successfactors_compensation_full | WARN | SUCCESSFACTORS_PERMISSION | docs/release-evidence/sap_successfactors_aws_live/SF_LIVE_20260610T220801Z/postgres_registry.json | Requiere entidades de compensacion bloqueadas por OData/scope |
| Gold dataset sap_successfactors_employees_anomalies | WARN | DATASET_NOT_MATERIALIZED | docs/release-evidence/sap_successfactors_aws_live/SF_LIVE_20260610T220801Z/postgres_registry.json | Dataset planeado, sin materializacion operativa en esta version |
| Gold dataset sap_successfactors_recruitment_funnel | WARN | DEPENDENCY_NOT_EXTRACTED | docs/release-evidence/sap_successfactors_aws_live/SF_LIVE_20260610T220801Z/postgres_registry.json | Requiere detalle de postulaciones no disponible en la extraccion actual |
| Gold dataset sap_successfactors_recruitment_pipeline | WARN | DEPENDENCY_NOT_EXTRACTED | docs/release-evidence/sap_successfactors_aws_live/SF_LIVE_20260610T220801Z/postgres_registry.json | Requiere detalle de postulaciones no disponible en la extraccion actual |
| Gold dataset sap_successfactors_turnover_by_period | WARN | AUTH_SCOPE_BLOCKED | docs/release-evidence/sap_successfactors_aws_live/SF_LIVE_20260610T220801Z/postgres_registry.json | Requiere EmpEmploymentTermination bloqueada por scope OAuth/OData |
| Control Room real | PASS | NONE | docs/release-evidence/sap_successfactors_aws_live/SF_LIVE_20260610T220801Z/commands/18-control-room-apps-kb-agents-real.log |  |
| Studio/copiloto real | PASS | NONE | docs/release-evidence/sap_successfactors_aws_live/SF_LIVE_20260610T220801Z/commands/19-studio-copiloto-real.log | turnos ejecutados=16 |

## Entidades con datos

PerNationalId, FOCompany, FODepartment, FODivision, FOLocation, FOBusinessUnit, FOCostCenter, FOJobCode, PerformanceReview, employee_360, org_structure, headcount_by_department, headcount_by_location, headcount_by_company, manager_hierarchy

## Entidades vacias

PerPerson, PerPersonal, PerEmail, EmpEmployment, EmpJob, PaymentInformationDetailV3, JobRequisition, compensation_distribution

## Entidades con error

User, PerPhone, PerAddressDEFLT, EmpCompensation, EmpPayCompRecurring, EmpPayCompNonRecurring, EmpEmploymentTermination, Position, EmployeeTime, TimeAccount, WorkSchedule, Candidate, GoalPlan, LearningItem, EmpJob_History, compensation_full, recruitment_pipeline, recruitment_funnel, turnover_by_period, employees_anomalies

## Entidades no ejecutadas

Ninguna.

## Evidencia

- Directorio: `docs/release-evidence/sap_successfactors_aws_live/SF_LIVE_20260610T220801Z`
- Matriz CSV: `docs/release-evidence/sap_successfactors_aws_live/SF_LIVE_20260610T220801Z/coverage_matrix.csv`
- Matriz Markdown: `docs/release-evidence/sap_successfactors_aws_live/SF_LIVE_20260610T220801Z/coverage_matrix.md`
- S3: `docs/release-evidence/sap_successfactors_aws_live/SF_LIVE_20260610T220801Z/s3_lakehouse_summary.json`
- Postgres/RDS: `docs/release-evidence/sap_successfactors_aws_live/SF_LIVE_20260610T220801Z/postgres_registry.json`
- Control Room/Apps/KB/Agents: `docs/release-evidence/sap_successfactors_aws_live/SF_LIVE_20260610T220801Z/console_surfaces.json`
- Copiloto: `docs/release-evidence/sap_successfactors_aws_live/SF_LIVE_20260610T220801Z/copilot_validation.json`

## Comando unico para repetir

```bash
make sap-successfactors-aws-live-max
```
