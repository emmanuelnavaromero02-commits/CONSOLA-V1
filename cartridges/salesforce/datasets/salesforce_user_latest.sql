-- salesforce_user_latest  (silver)  cartridge: salesforce
-- sources: ["raw/salesforce/User"]
-- description: Última versión de cada usuario / vendedor (dedup por Id). email/username masked en bronze.
SELECT DISTINCT ON (Id)
    Id                        AS user_id,
    Name                      AS user_name,
    Email                     AS email,
    Username                  AS username,
    Title                     AS title,
    Department                AS department,
    ManagerId                 AS manager_id,
    UserRoleId                AS user_role_id,
    CAST(IsActive AS BOOLEAN) AS is_active,
    load_date
FROM read_parquet('s3://{bucket}/raw/salesforce/User/**/*.parquet',
                  hive_partitioning = true, union_by_name = true)
ORDER BY Id, load_date DESC
