# PR #270 AWS Evidence - SuccessFactors Gold Foundation

Run date: 2026-06-07 UTC

Branch: `codex/sf-gold-foundation-schedule`

AWS validation commits:

- `b99301a` - initial PR #270 foundation schedule migration.
- `798b2bd` - aligned headcount/manager/turnover Gold source metadata to scoped Silver sources.
- `da50031` - declared missing `fobusinessunit_latest` source for org structure.
- `039c376` - treated open-ended employment as active.
- `a320349` - treated missing SuccessFactors employment dates as active.
- `1d60a91` - parsed SAP OData `/Date(ms)/` values in `EmpEmployment` so terminated employees are not counted as active.

AWS stack:

```text
mode_console              ghcr.io/emmanuelnavaromero02-commits/console:v1.45.30-beta              healthy
mode_sap_successfactors   ghcr.io/emmanuelnavaromero02-commits/sap_successfactors:v1.45.30-beta   healthy
mode_airflow_scheduler    ghcr.io/emmanuelnavaromero02-commits/airflow:v1.45.30-beta              healthy
mode_airflow              ghcr.io/emmanuelnavaromero02-commits/airflow:v1.45.30-beta              healthy
mode_refinement           ghcr.io/emmanuelnavaromero02-commits/refinement:v1.45.30-beta           healthy
```

Console readiness:

```json
{"ok":true,"service":"console"}
```

Migration applied:

```text
99l_sap_successfactors_gold_foundation_schedule.sql
```

Scheduled FEMSA entities in `entity_config` use:

- `connection_id = femsa_sf`
- `tenant_id = b95f4d58-c9c8-4fd5-8d07-ddde294c7d78`
- `workspace_id = a2b1ced2-4d92-4bbe-8f9f-9a7cc88bb9f4`

## Bronze + Silver Extraction

Triggered through Airflow DAG `sap_successfactors_extract` with `conn_id=femsa_sf` and FEMSA scope.

Successful live SF extractions:

```text
FOCompany            success
FODepartment         success
FODivision           success
FOBusinessUnit       success
FOJobCode            success
```

Silver row counts after extraction:

```text
sap_successfactors_focompany_latest        48
sap_successfactors_fodepartment_latest     527
sap_successfactors_fodivision_latest       28
sap_successfactors_fobusinessunit_latest   8
sap_successfactors_fojobcode_latest        63
```

Blocked live extraction:

```text
EmpEmploymentTermination: up_for_retry
First observed cartridge log: /oauth/token returned 400 before OData request.
Airflow task state after retry: up_for_retry
Silver sap_successfactors_empemploymenttermination_latest: no row_count / no last_refresh
```

## Materialized Silver / Gold

Final AWS materialization output:

```text
sap_successfactors_employee_360
row_count=1362
storage_uri=s3://modecissions-lakehouse-783792/silver/sap_successfactors/sap_successfactors_employee_360/tenant_id=b95f4d58-c9c8-4fd5-8d07-ddde294c7d78/workspace_id=a2b1ced2-4d92-4bbe-8f9f-9a7cc88bb9f4/_snapshots/20260607T091000470319Z-58b00b6cdbdb.parquet

sap_successfactors_headcount_by_location
row_count=95
storage_uri=s3://modecissions-lakehouse-783792/gold/sap_successfactors/sap_successfactors_headcount_by_location/tenant_id=b95f4d58-c9c8-4fd5-8d07-ddde294c7d78/workspace_id=a2b1ced2-4d92-4bbe-8f9f-9a7cc88bb9f4/_snapshots/20260607T091002596339Z-8a673d7c5b6e.parquet

sap_successfactors_headcount_by_department
row_count=316
storage_uri=s3://modecissions-lakehouse-783792/gold/sap_successfactors/sap_successfactors_headcount_by_department/tenant_id=b95f4d58-c9c8-4fd5-8d07-ddde294c7d78/workspace_id=a2b1ced2-4d92-4bbe-8f9f-9a7cc88bb9f4/_snapshots/20260607T091003407660Z-fb958cb05d28.parquet

sap_successfactors_headcount_by_company
row_count=44
storage_uri=s3://modecissions-lakehouse-783792/gold/sap_successfactors/sap_successfactors_headcount_by_company/tenant_id=b95f4d58-c9c8-4fd5-8d07-ddde294c7d78/workspace_id=a2b1ced2-4d92-4bbe-8f9f-9a7cc88bb9f4/_snapshots/20260607T091004107080Z-602e87b85c2d.parquet

sap_successfactors_org_structure
row_count=515
storage_uri=s3://modecissions-lakehouse-783792/silver/sap_successfactors/sap_successfactors_org_structure/tenant_id=b95f4d58-c9c8-4fd5-8d07-ddde294c7d78/workspace_id=a2b1ced2-4d92-4bbe-8f9f-9a7cc88bb9f4/_snapshots/20260607T091005087701Z-e55a3e15c5ca.parquet

sap_successfactors_manager_hierarchy
row_count=1288
storage_uri=s3://modecissions-lakehouse-783792/gold/sap_successfactors/sap_successfactors_manager_hierarchy/tenant_id=b95f4d58-c9c8-4fd5-8d07-ddde294c7d78/workspace_id=a2b1ced2-4d92-4bbe-8f9f-9a7cc88bb9f4/_snapshots/20260607T091009637027Z-15ff9d1edde5.parquet
```

## Employment Active Rule Validation

Initial validation showed `TRY_CAST('/Date(ms)/' AS DATE)` was losing SAP OData dates and making all `start_date` / `end_date` values `NULL` in Silver. The PR now parses `/Date(ms)/` into proper dates in `sap_successfactors_empemployment_latest`.

Exact latest snapshot status:

```json
{
  "active_closed_dates": 3,
  "null_dates": 1285,
  "already_terminated": 74,
  "total": 1362,
  "min_start": "1990-09-03",
  "max_start": "2026-03-19",
  "min_end": "2016-06-09",
  "max_end": "2030-12-31"
}
```

Exact latest `employee_360` status after the fix:

```json
{
  "active_employee_360": 1288,
  "inactive_employee_360": 74,
  "total": 1362
}
```

Interpretation:

- The 74 rows with `end_date < CURRENT_DATE` are excluded from active counts.
- The 1285 rows with `end_date IS NULL` have valid parsed `start_date` values and are treated as open-ended active employment.
- Cross-check against `EmpEmploymentTermination` is still pending because that extraction is blocked by SuccessFactors `/oauth/token` returning 400.

Not materialized:

```text
sap_successfactors_turnover_by_period
Reason: depends on sap_successfactors_empemploymenttermination_latest, which did not materialize because EmpEmploymentTermination extraction is still blocked by SF /oauth/token 400.
```

## Pending Gold Families

Do not force these until their source entities are extracted live:

- Compensation: pending `EmpCompensation`, `EmpPayCompRecurring`, `EmpPayCompNonRecurring`.
- Recruitment: pending `JobRequisition`, `Candidate`.
- Turnover: pending successful `EmpEmploymentTermination` token/extraction.

## Local Validation

```text
.venv/bin/pytest tests/test_sap_successfactors_datasets.py tests/test_sap_successfactors_silver_schedule.py tests/test_sap_successfactors_gold_foundation_schedule.py cartridges/sap_successfactors/tests/test_entity_config_contract.py -q
20 passed in 0.28s

.venv/bin/pytest cartridges/sap_successfactors/tests -q
62 passed, 1 skipped in 4.91s

.venv/bin/pytest tests -k "sap_successfactors" -q
162 passed, 1 skipped, 2241 deselected in 9.55s
```
