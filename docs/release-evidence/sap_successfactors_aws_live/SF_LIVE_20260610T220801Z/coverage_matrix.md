# Matriz de cobertura live SAP SuccessFactors

- run_id: `SF_LIVE_20260610T220801Z`
- fecha: `2026-06-10T22:13:03Z`

| Entidad | Extraccion | Filas | Estado | Run | Bronze | Silver | Gold | Error |
|---|---:|---:|---|---|---|---|---|---|
| User | yes |  | failed-open | 1bcdb6a6-acf9-4777-b0c4-ce3c1a7d2a49 |  | sap_successfactors_user_latest |  | ODATA_FILTER_OR_SCOPE |
| PerPerson | yes | 0 | empty-valid | 5c45abe9-9f2f-4929-aeb2-92eed788f9a9 | s3://modecissions-lakehouse-783792/raw/sap_successfactors/PerPerson/tenant_id=b95f4d58-c9c8-4fd5-8d07-ddde294c7d78/works | sap_successfactors_perperson_latest |  |  |
| PerPersonal | yes | 0 | empty-valid | 1f6aa76a-c7bc-4811-b633-de85d1d08087 | s3://modecissions-lakehouse-783792/raw/sap_successfactors/PerPersonal/tenant_id=b95f4d58-c9c8-4fd5-8d07-ddde294c7d78/wor | sap_successfactors_perpersonal_latest |  |  |
| PerEmail | yes | 0 | empty-valid | e4f7affb-7a9f-4869-a62c-c567b969c9a7 | s3://modecissions-lakehouse-783792/raw/sap_successfactors/PerEmail/tenant_id=b95f4d58-c9c8-4fd5-8d07-ddde294c7d78/worksp | sap_successfactors_peremail_latest |  |  |
| PerPhone | yes |  | failed-open | d06c7473-f9f0-4b8c-a29a-e038b7d8c536 |  | sap_successfactors_perphone_latest |  | ODATA_FILTER_OR_SCOPE |
| PerAddressDEFLT | yes |  | failed-open | 8fd7c5a3-5faa-4290-a2c3-7c9d83b99120 |  | sap_successfactors_peraddressdeflt_latest |  | ODATA_FILTER_OR_SCOPE |
| PerNationalId | yes | 939 | extracted | ae6cb5ea-c52b-4076-86d7-e70e8b87d997 | s3://modecissions-lakehouse-783792/raw/sap_successfactors/PerNationalId/tenant_id=b95f4d58-c9c8-4fd5-8d07-ddde294c7d78/w | sap_successfactors_pernationalid_latest |  |  |
| EmpEmployment | yes | 0 | empty-valid | a56cdd85-3e74-46c9-b461-bcd57b35d079 | s3://modecissions-lakehouse-783792/raw/sap_successfactors/EmpEmployment/tenant_id=b95f4d58-c9c8-4fd5-8d07-ddde294c7d78/w | sap_successfactors_empemployment_latest |  |  |
| EmpJob | yes | 0 | empty-valid | ff812dee-a143-4f42-abdc-b26e5de30ebd | s3://modecissions-lakehouse-783792/raw/sap_successfactors/EmpJob/tenant_id=b95f4d58-c9c8-4fd5-8d07-ddde294c7d78/workspac | sap_successfactors_empjob_latest |  |  |
| EmpCompensation | yes |  | failed-open | 678807cc-2fa5-46db-819a-88f4030754ee |  | sap_successfactors_empcompensation_latest |  | ODATA_FILTER_OR_SCOPE |
| EmpPayCompRecurring | yes |  | failed-open | 8850546b-4e15-4e78-8937-90a641d49c2f |  | sap_successfactors_emppaycomprecurring_latest |  | ODATA_FILTER_OR_SCOPE |
| EmpPayCompNonRecurring | yes |  | failed-open | d122077f-1e37-4c29-9b61-d2c604374fe0 |  | sap_successfactors_emppaycompnonrecurring_latest |  | ODATA_FILTER_OR_SCOPE |
| PaymentInformationDetailV3 | yes | 0 | empty-valid | b2ded226-d82a-4f76-bb54-117ae866655c | s3://modecissions-lakehouse-783792/raw/sap_successfactors/PaymentInformationDetailV3/tenant_id=b95f4d58-c9c8-4fd5-8d07-d | sap_successfactors_paymentinformationdetailv3_latest |  |  |
| EmpEmploymentTermination | yes |  | failed-open | b2199d56-5d24-41d5-99d5-1320158779a9 |  | sap_successfactors_empemploymenttermination_latest |  | ODATA_FILTER_OR_SCOPE |
| FOCompany | yes | 48 | extracted | 7d6ae1f9-f338-49d4-aa5c-1489a2dc72fa | s3://modecissions-lakehouse-783792/raw/sap_successfactors/FOCompany/tenant_id=b95f4d58-c9c8-4fd5-8d07-ddde294c7d78/works | sap_successfactors_focompany_latest |  |  |
| FODepartment | yes | 527 | extracted | c2654eec-82fa-4118-ad03-d8035d5f237e | s3://modecissions-lakehouse-783792/raw/sap_successfactors/FODepartment/tenant_id=b95f4d58-c9c8-4fd5-8d07-ddde294c7d78/wo | sap_successfactors_fodepartment_latest |  |  |
| FODivision | yes | 28 | extracted | d4f20d48-c7a6-4d82-a7ee-f0047e341f2c | s3://modecissions-lakehouse-783792/raw/sap_successfactors/FODivision/tenant_id=b95f4d58-c9c8-4fd5-8d07-ddde294c7d78/work | sap_successfactors_fodivision_latest |  |  |
| FOLocation | yes | 199 | extracted | 377359f4-4bd9-4faa-a0a5-0a9bcd2844fa | s3://modecissions-lakehouse-783792/raw/sap_successfactors/FOLocation/tenant_id=b95f4d58-c9c8-4fd5-8d07-ddde294c7d78/work | sap_successfactors_folocation_latest |  |  |
| FOBusinessUnit | yes | 8 | extracted | c712f710-5d32-499a-bea8-95f6a17a3ca8 | s3://modecissions-lakehouse-783792/raw/sap_successfactors/FOBusinessUnit/tenant_id=b95f4d58-c9c8-4fd5-8d07-ddde294c7d78/ | sap_successfactors_fobusinessunit_latest |  |  |
| FOCostCenter | yes | 443 | extracted | 89fbd593-1744-4023-b4e4-bc4776ad678c | s3://modecissions-lakehouse-783792/raw/sap_successfactors/FOCostCenter/tenant_id=b95f4d58-c9c8-4fd5-8d07-ddde294c7d78/wo | sap_successfactors_focostcenter_latest |  |  |
| FOJobCode | yes | 63 | extracted | 6086bbe1-f004-4f52-aeb5-6c562a511ebd | s3://modecissions-lakehouse-783792/raw/sap_successfactors/FOJobCode/tenant_id=b95f4d58-c9c8-4fd5-8d07-ddde294c7d78/works | sap_successfactors_fojobcode_latest |  |  |
| Position | yes |  | failed-open | e5a1e49a-f829-46ef-9323-61ce0d2ee0d3 |  | sap_successfactors_position_latest |  | ODATA_FILTER_OR_SCOPE |
| EmployeeTime | yes |  | failed-open | 94e9bea3-6d4e-4876-9553-48c37db84aaf |  | sap_successfactors_employeetime_latest |  | ODATA_FILTER_OR_SCOPE |
| TimeAccount | yes |  | failed-open | dcefa9a9-aa9c-49fd-8721-fa39bd2fbb31 |  | sap_successfactors_timeaccount_latest |  | ODATA_FILTER_OR_SCOPE |
| WorkSchedule | yes |  | failed-open | e63c830a-adea-4b47-a7c5-1f88df46d92c |  | sap_successfactors_workschedule_latest |  | ODATA_FILTER_OR_SCOPE |
| JobRequisition | yes | 0 | empty-valid | 6af59c4e-ecbb-4954-82ee-0a5c6622660e | s3://modecissions-lakehouse-783792/raw/sap_successfactors/JobRequisition/tenant_id=b95f4d58-c9c8-4fd5-8d07-ddde294c7d78/ | sap_successfactors_jobrequisition_latest |  |  |
| Candidate | yes |  | failed-open | 1d4f7c89-4964-4246-afce-9b40276a6e60 |  | sap_successfactors_candidate_latest |  | SUCCESSFACTORS_PERMISSION |
| GoalPlan | yes |  | failed-open | 212ff803-4559-4d00-b51d-f544f5dad4d5 |  | sap_successfactors_goalplan_latest |  | SUCCESSFACTORS_NOT_AVAILABLE |
| PerformanceReview | yes | 1 | extracted | fc975a64-96c7-4a9c-9174-6b5dcbd0e223 | s3://modecissions-lakehouse-783792/raw/sap_successfactors/PerformanceReview/tenant_id=b95f4d58-c9c8-4fd5-8d07-ddde294c7d | sap_successfactors_performancereview_latest |  |  |
| LearningItem | yes |  | failed-open | ac3fd1b4-f3f8-4adc-8a57-36832c115ad8 |  | sap_successfactors_learningitem_latest |  | SUCCESSFACTORS_NOT_AVAILABLE |
| EmpJob_History | yes |  | failed-open | 349791fe-dc2c-4895-aac9-2c977cc9b469 |  | sap_successfactors_empjob_history_latest |  | ODATA_FILTER_OR_SCOPE |
| employee_360 | not-executed | 1362 | gold-ready |  |  |  | sap_successfactors_employee_360 |  |
| org_structure | not-executed | 555 | gold-ready |  |  |  | sap_successfactors_org_structure |  |
| compensation_full | not-executed |  | failed-open |  |  |  | sap_successfactors_compensation_full | Requiere entidades de compensacion bloqueadas por OData/scope |
| recruitment_pipeline | not-executed |  | failed-open |  |  |  | sap_successfactors_recruitment_pipeline | Requiere detalle de postulaciones no disponible en la extraccion actual |
| headcount_by_department | not-executed | 314 | gold-ready |  |  |  | sap_successfactors_headcount_by_department |  |
| headcount_by_location | not-executed | 96 | gold-ready |  |  |  | sap_successfactors_headcount_by_location |  |
| headcount_by_company | not-executed | 44 | gold-ready |  |  |  | sap_successfactors_headcount_by_company |  |
| compensation_distribution | not-executed | 0 | empty-valid |  |  |  | sap_successfactors_compensation_distribution |  |
| recruitment_funnel | not-executed |  | failed-open |  |  |  | sap_successfactors_recruitment_funnel | Requiere detalle de postulaciones no disponible en la extraccion actual |
| turnover_by_period | not-executed |  | failed-open |  |  |  | sap_successfactors_turnover_by_period | Requiere EmpEmploymentTermination bloqueada por scope OAuth/OData |
| manager_hierarchy | not-executed | 1288 | gold-ready |  |  |  | sap_successfactors_manager_hierarchy |  |
| employees_anomalies | not-executed |  | failed-open |  |  |  | sap_successfactors_employees_anomalies | Dataset planeado, sin materializacion operativa en esta version |
