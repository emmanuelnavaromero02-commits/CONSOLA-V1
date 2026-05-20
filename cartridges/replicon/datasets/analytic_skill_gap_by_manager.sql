-- analytic_skill_gap_by_manager  (gold)  cartridge: replicon
-- sources: ["gold/replicon/fact_empleado_skills","gold/replicon/empleados_maestro"]
-- description: Heatmap ejecutivo de brechas de skills por manager y categoria.

WITH skills AS (
    SELECT
        LOWER(TRIM(usuario)) AS usuario_key,
        COALESCE(NULLIF(TRIM(supervisor), ''), 'Sin manager') AS manager_name,
        COALESCE(NULLIF(TRIM(skill_category), ''), 'Sin categoria') AS skill_category,
        TRY_CAST(skill_rating AS DOUBLE) AS skill_rating,
        COALESCE(is_mentor, FALSE) AS is_mentor
    FROM read_parquet(
        's3://{bucket}/gold/replicon/fact_empleado_skills/data.parquet',
        hive_partitioning=true,
        union_by_name=true
    )
    WHERE usuario IS NOT NULL
      AND skill_name IS NOT NULL
),
team AS (
    SELECT
        LOWER(TRIM(usuario)) AS usuario_key,
        COALESCE(NULLIF(TRIM(supervisor), ''), 'Sin manager') AS manager_name
    FROM read_parquet(
        's3://{bucket}/gold/replicon/empleados_maestro/data.parquet',
        hive_partitioning=true,
        union_by_name=true
    )
    WHERE usuario IS NOT NULL
),
team_sizes AS (
    SELECT manager_name, COUNT(DISTINCT usuario_key) AS total_empleados_en_equipo
    FROM team
    GROUP BY 1
),
agg AS (
    SELECT
        manager_name,
        skill_category,
        ROUND(AVG(skill_rating), 2) AS avg_rating,
        COUNT(DISTINCT usuario_key) AS empleados_con_skills,
        COUNT(DISTINCT CASE WHEN skill_rating >= 4 THEN usuario_key END) AS count_experts,
        COUNT(DISTINCT CASE WHEN skill_rating >= 5 OR is_mentor THEN usuario_key END) AS count_mentors
    FROM skills
    WHERE skill_rating IS NOT NULL
    GROUP BY 1, 2
)
SELECT
    a.manager_name,
    a.skill_category,
    a.avg_rating,
    a.empleados_con_skills,
    COALESCE(ts.total_empleados_en_equipo, a.empleados_con_skills) AS total_empleados_en_equipo,
    GREATEST(COALESCE(ts.total_empleados_en_equipo, a.empleados_con_skills) - a.empleados_con_skills, 0) AS empleados_sin_evaluacion,
    a.count_experts,
    a.count_mentors,
    CASE
        WHEN a.avg_rating < 2 THEN 'CRITICA'
        WHEN a.avg_rating <= 3 THEN 'MODERADA'
        ELSE 'SATISFACTORIA'
    END AS brecha_nivel
FROM agg a
LEFT JOIN team_sizes ts
    ON ts.manager_name = a.manager_name
ORDER BY a.manager_name, a.skill_category
