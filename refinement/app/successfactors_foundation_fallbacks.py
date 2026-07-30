from __future__ import annotations

FOUNDATION_GOLD_FALLBACK_SQL: dict[str, str] = {
    "sap_successfactors_employee_360": """
SELECT
    NULL::VARCHAR AS user_id,
    NULL::VARCHAR AS full_name,
    NULL::VARCHAR AS gender,
    NULL::VARCHAR AS marital_status,
    NULL::VARCHAR AS company_id,
    NULL::VARCHAR AS company_name,
    NULL::VARCHAR AS division_id,
    NULL::VARCHAR AS division_name,
    NULL::VARCHAR AS department_id,
    NULL::VARCHAR AS department_name,
    NULL::VARCHAR AS location_id,
    NULL::VARCHAR AS location_name,
    NULL::VARCHAR AS job_code,
    NULL::VARCHAR AS cost_center,
    NULL::VARCHAR AS manager_id,
    NULL::DATE AS start_date,
    NULL::DATE AS end_date,
    FALSE::BOOLEAN AS is_active
WHERE FALSE
""",
    "sap_successfactors_org_structure": """
SELECT
    NULL::VARCHAR AS company_id,
    NULL::VARCHAR AS company_name,
    NULL::VARCHAR AS division_id,
    NULL::VARCHAR AS division_name,
    NULL::VARCHAR AS department_id,
    NULL::VARCHAR AS department_name,
    NULL::VARCHAR AS location_id,
    NULL::VARCHAR AS location_name,
    NULL::VARCHAR AS business_unit_id,
    NULL::VARCHAR AS business_unit_name
WHERE FALSE
""",
    "sap_successfactors_headcount_by_location": """
SELECT
    NULL::VARCHAR AS location_id,
    NULL::VARCHAR AS location_name,
    0::BIGINT AS headcount,
    CAST(DATE_TRUNC('month', CURRENT_DATE) AS DATE) AS snapshot_month
WHERE FALSE
""",
    "sap_successfactors_headcount_by_department": """
SELECT
    NULL::VARCHAR AS department_id,
    NULL::VARCHAR AS department_name,
    0::BIGINT AS headcount,
    CAST(DATE_TRUNC('month', CURRENT_DATE) AS DATE) AS snapshot_month
WHERE FALSE
""",
    "sap_successfactors_headcount_by_company": """
SELECT
    NULL::VARCHAR AS company_id,
    NULL::VARCHAR AS company_name,
    0::BIGINT AS headcount,
    CAST(DATE_TRUNC('month', CURRENT_DATE) AS DATE) AS snapshot_month
WHERE FALSE
""",
    "sap_successfactors_manager_hierarchy": """
SELECT
    NULL::VARCHAR AS user_id,
    NULL::VARCHAR AS full_name,
    NULL::VARCHAR AS manager_id,
    0::BIGINT AS direct_reports,
    0::BIGINT AS depth
WHERE FALSE
""",
}
