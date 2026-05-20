-- fact_empleado_skills  (gold)  cartridge: replicon
-- sources: ["raw/replicon/UserSkills","raw/replicon/User"]
-- description: Skills por empleado con rating, categoria, supervisor y tipo de empleado para matching y heatmaps.

WITH latest_user_skills AS (
    SELECT to_json(us) AS row_json
    FROM read_parquet(
        's3://{bucket}/raw/replicon/UserSkills/**/*.parquet',
        hive_partitioning=true,
        union_by_name=true
    ) us
    WHERE load_date = (
        SELECT MAX(load_date)
        FROM read_parquet(
            's3://{bucket}/raw/replicon/UserSkills/**/*.parquet',
            hive_partitioning=true,
            union_by_name=true
        )
    )
),
latest_users AS (
    SELECT to_json(u) AS row_json
    FROM read_parquet(
        's3://{bucket}/raw/replicon/User/**/*.parquet',
        hive_partitioning=true,
        union_by_name=true
    ) u
    WHERE load_date = (
        SELECT MAX(load_date)
        FROM read_parquet(
            's3://{bucket}/raw/replicon/User/**/*.parquet',
            hive_partitioning=true,
            union_by_name=true
        )
    )
),
skills AS (
    SELECT
        NULLIF(TRIM(COALESCE(
            json_extract_string(row_json, '$."User Name"'),
            json_extract_string(row_json, '$.username'),
            json_extract_string(row_json, '$.user_name')
        )), '') AS usuario,
        NULLIF(TRIM(COALESCE(
            json_extract_string(row_json, '$."Skill Name"'),
            json_extract_string(row_json, '$.skill_name')
        )), '') AS skill_name,
        COALESCE(
            TRY_CAST(json_extract_string(row_json, '$."Skill Rating"') AS DOUBLE),
            TRY_CAST(json_extract_string(row_json, '$."Rating"') AS DOUBLE),
            TRY_CAST(json_extract_string(row_json, '$.skill_rating') AS DOUBLE),
            TRY_CAST(json_extract_string(row_json, '$.rating') AS DOUBLE),
            0.0
        ) AS skill_rating,
        COALESCE(
            NULLIF(TRIM(json_extract_string(row_json, '$."Skill Category"')), ''),
            NULLIF(TRIM(json_extract_string(row_json, '$."Category"')), ''),
            NULLIF(TRIM(json_extract_string(row_json, '$.skill_category')), ''),
            'Sin categoria'
        ) AS skill_category,
        NULLIF(TRIM(json_extract_string(row_json, '$."Tipo de Proveedor"')), '') AS tipo_de_proveedor_raw,
        COALESCE(
            TRY_CAST(json_extract_string(row_json, '$."Is Mentor"') AS BOOLEAN),
            TRY_CAST(json_extract_string(row_json, '$.is_mentor') AS BOOLEAN),
            FALSE
        ) AS mentor_flag
    FROM latest_user_skills
),
users AS (
    SELECT
        NULLIF(TRIM(COALESCE(
            json_extract_string(row_json, '$.username'),
            json_extract_string(row_json, '$."User Name"')
        )), '') AS usuario,
        COALESCE(NULLIF(TRIM(json_extract_string(row_json, '$.employeetypename')), ''), 'Unknown') AS tipo_empleado,
        COALESCE(NULLIF(TRIM(json_extract_string(row_json, '$.currentsupervisorusername')), ''), '') AS supervisor,
        COALESCE(NULLIF(TRIM(json_extract_string(row_json, '$.departmentname')), ''), '') AS departamento
    FROM latest_users
)
SELECT
    s.usuario,
    s.skill_name,
    s.skill_rating,
    s.skill_category,
    COALESCE(u.tipo_empleado, s.tipo_de_proveedor_raw, 'Unknown') AS tipo_empleado,
    COALESCE(u.supervisor, '') AS supervisor,
    COALESCE(u.departamento, '') AS departamento,
    COALESCE(s.tipo_de_proveedor_raw, '') AS tipo_de_proveedor,
    (s.mentor_flag OR s.skill_rating >= 5) AS is_mentor
FROM skills s
LEFT JOIN users u
    ON LOWER(TRIM(u.usuario)) = LOWER(TRIM(s.usuario))
WHERE s.usuario IS NOT NULL
  AND s.skill_name IS NOT NULL
