from __future__ import annotations

TALENT_READFREE_EMPTY_SQL: dict[str, str] = {
    "sap_successfactors_performance_cycle": """
SELECT
    NULL::VARCHAR AS user_id,
    NULL::VARCHAR AS user_id_hash,
    NULL::VARCHAR AS form_data_id,
    NULL::VARCHAR AS form_template_id,
    NULL::VARCHAR AS review_status,
    NULL::DOUBLE AS performance_rating,
    NULL::DOUBLE AS potential_rating,
    NULL::DATE AS cycle_start_date,
    NULL::DATE AS cycle_end_date,
    0::BIGINT AS goals_total,
    0::BIGINT AS goals_completed,
    NULL::DOUBLE AS goals_percent_complete_avg,
    NULL::VARCHAR AS performance_status,
    NULL::VARCHAR AS load_date
WHERE FALSE
""",
    "sap_successfactors_employee_competency": """
SELECT
    NULL::VARCHAR AS user_id,
    NULL::VARCHAR AS skill_record_id,
    NULL::VARCHAR AS skill_id,
    NULL::VARCHAR AS skill_name,
    NULL::DOUBLE AS proficiency_score,
    NULL::DOUBLE AS proficiency_100,
    NULL::VARCHAR AS competency_status,
    NULL::VARCHAR AS source_entity,
    NULL::VARCHAR AS load_date
WHERE FALSE
""",
    "sap_successfactors_employee_aspiration": """
SELECT
    NULL::VARCHAR AS user_id,
    NULL::VARCHAR AS aspiration_record_id,
    NULL::VARCHAR AS target_role,
    NULL::VARCHAR AS readiness,
    NULL::DOUBLE AS aspiration_100,
    NULL::VARCHAR AS mobility_preference,
    NULL::VARCHAR AS source_entity,
    NULL::VARCHAR AS aspiration_status,
    NULL::VARCHAR AS load_date
WHERE FALSE
""",
    "sap_successfactors_role_requirements": """
SELECT
    NULL::VARCHAR AS role_id,
    NULL::VARCHAR AS role_name,
    NULL::VARCHAR AS job_code,
    NULL::VARCHAR AS department,
    NULL::VARCHAR AS location,
    NULL::VARCHAR AS cost_center,
    0::BIGINT AS competency_catalog_count,
    NULL::VARCHAR AS required_skills_status,
    NULL::VARCHAR AS blockers,
    NULL::VARCHAR AS load_date
WHERE FALSE
""",
    "sap_successfactors_learning_completion": """
SELECT
    NULL::VARCHAR AS learning_event_id,
    NULL::VARCHAR AS user_id,
    NULL::VARCHAR AS learning_item_id,
    NULL::VARCHAR AS title,
    NULL::VARCHAR AS status,
    NULL::DATE AS due_date,
    NULL::DATE AS completion_date,
    NULL::DOUBLE AS credit_hours,
    NULL::BOOLEAN AS completed,
    NULL::BOOLEAN AS overdue,
    NULL::VARCHAR AS source_entity,
    NULL::VARCHAR AS load_date
WHERE FALSE
""",
    "sap_successfactors_job_application_pipeline": """
SELECT
    NULL::VARCHAR AS job_req_id,
    NULL::VARCHAR AS job_title,
    NULL::VARCHAR AS requisition_status,
    NULL::VARCHAR AS department,
    NULL::VARCHAR AS location,
    NULL::VARCHAR AS application_id,
    NULL::VARCHAR AS candidate_id,
    NULL::VARCHAR AS application_status,
    NULL::VARCHAR AS source,
    NULL::VARCHAR AS candidate_status,
    NULL::VARCHAR AS load_date
WHERE FALSE
""",
    "sap_successfactors_movement_events": """
SELECT
    NULL::VARCHAR AS user_id,
    NULL::DATE AS event_date,
    NULL::VARCHAR AS job_code,
    NULL::VARCHAR AS position,
    NULL::VARCHAR AS department,
    NULL::VARCHAR AS location,
    NULL::VARCHAR AS manager_id,
    NULL::VARCHAR AS event_reason,
    NULL::VARCHAR AS event_reason_name,
    NULL::VARCHAR AS event,
    NULL::VARCHAR AS event_reason_category,
    NULL::VARCHAR AS movement_type,
    NULL::VARCHAR AS load_date
WHERE FALSE
""",
    "sap_successfactors_recruitment_pipeline": """
SELECT
    NULL::VARCHAR AS job_req_id,
    NULL::VARCHAR AS job_title,
    NULL::VARCHAR AS status,
    NULL::VARCHAR AS department,
    NULL::VARCHAR AS location,
    0::BIGINT AS applications_total,
    0::BIGINT AS candidate_pool_total,
    NULL::VARCHAR AS recruiting_status
WHERE FALSE
""",
    "sap_successfactors_compensation_full": """
SELECT
    NULL::VARCHAR AS user_id,
    NULL::VARCHAR AS pay_group,
    NULL::VARCHAR AS frequency_code,
    NULL::VARCHAR AS component_kind,
    NULL::VARCHAR AS pay_component,
    NULL::VARCHAR AS paycomp_value,
    NULL::VARCHAR AS currency
WHERE FALSE
""",
    "sap_successfactors_talent_employee_profile": """
SELECT
    NULL::VARCHAR AS tenant_id,
    NULL::VARCHAR AS workspace_id,
    NULL::VARCHAR AS user_id,
    NULL::VARCHAR AS full_name,
    NULL::VARCHAR AS company_id,
    NULL::VARCHAR AS company_name,
    NULL::VARCHAR AS division_id,
    NULL::VARCHAR AS division_name,
    NULL::VARCHAR AS department_id,
    NULL::VARCHAR AS department_name,
    NULL::VARCHAR AS location_id,
    NULL::VARCHAR AS location_name,
    NULL::VARCHAR AS job_code,
    NULL::VARCHAR AS manager_id,
    0::BIGINT AS direct_reports,
    0::BIGINT AS hierarchy_depth,
    NULL::VARCHAR AS start_date,
    NULL::VARCHAR AS end_date,
    NULL::BIGINT AS tenure_months,
    NULL::DOUBLE AS competency_score,
    NULL::DOUBLE AS performance_score,
    NULL::DOUBLE AS aspiration_score,
    NULL::BOOLEAN AS invalid_performance_input,
    NULL::BOOLEAN AS invalid_competency_input,
    NULL::BOOLEAN AS invalid_aspiration_input,
    NULL::BOOLEAN AS invalid_score_input,
    NULL::VARCHAR AS cpa_status,
    NULL::VARCHAR AS profile_status,
    NULL::VARCHAR AS blockers,
    CURRENT_TIMESTAMP AS generated_at
WHERE FALSE
""",
    "sap_successfactors_talent_cpa_scores": """
SELECT
    NULL::VARCHAR AS tenant_id,
    NULL::VARCHAR AS workspace_id,
    NULL::VARCHAR AS user_id,
    NULL::VARCHAR AS full_name,
    NULL::VARCHAR AS company_name,
    NULL::VARCHAR AS department_name,
    NULL::VARCHAR AS location_name,
    NULL::VARCHAR AS job_code,
    0::BIGINT AS direct_reports,
    NULL::BIGINT AS tenure_months,
    NULL::VARCHAR AS role_name,
    NULL::DOUBLE AS competency_score,
    NULL::DOUBLE AS performance_score,
    NULL::DOUBLE AS aspiration_score,
    NULL::DOUBLE AS competency_100,
    NULL::DOUBLE AS performance_100,
    NULL::DOUBLE AS aspiration_100,
    NULL::BOOLEAN AS invalid_score_input,
    NULL::DOUBLE AS fit_score,
    NULL::VARCHAR AS cpa_status,
    NULL::VARCHAR AS role_profile_status,
    NULL::VARCHAR AS required_skills_status,
    NULL::VARCHAR AS blockers,
    CURRENT_TIMESTAMP AS generated_at
WHERE FALSE
""",
}
