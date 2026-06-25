-- sap_successfactors_employee_competency  (silver)  cartridge: sap_successfactors
-- sources: ["silver/sap_successfactors/sap_successfactors_userskill_latest", "silver/sap_successfactors/sap_successfactors_skillprofile_latest", "silver/sap_successfactors/sap_successfactors_competencyentity_latest"]
-- description: Competencias/skills por empleado normalizadas para KB-COMPETENCIAS.

WITH user_skill AS (
    SELECT 'UserSkill' AS source_entity, *
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_userskill_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
skill_profile AS (
    SELECT 'SkillProfile' AS source_entity, *
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_skillprofile_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
skills AS (
    SELECT * FROM user_skill
    UNION ALL
    SELECT * FROM skill_profile
),
catalog AS (
    SELECT competency_id, competency_name
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_competencyentity_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
)
SELECT
    s.user_id,
    s.skill_record_id,
    s.skill_id,
    COALESCE(s.skill_name, c.competency_name, s.skill_id) AS skill_name,
    s.proficiency_score,
    CASE
        WHEN s.proficiency_score IS NULL THEN NULL
        WHEN s.proficiency_score <= 5 THEN s.proficiency_score * 20
        ELSE s.proficiency_score
    END AS proficiency_100,
    CASE
        WHEN s.proficiency_score IS NULL THEN 'insufficient_data'
        ELSE 'ready'
    END AS competency_status,
    s.source_entity,
    s.load_date
FROM skills s
LEFT JOIN catalog c ON c.competency_id = s.skill_id
ORDER BY s.user_id, s.skill_id
