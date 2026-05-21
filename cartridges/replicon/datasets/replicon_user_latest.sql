-- replicon_user_latest  (silver)  cartridge: replicon
-- description: Usuarios de Replicon con enriquecimiento de tipo_de_proveedor (primer proveedor del usuario, tomado de UserSkills). Se mantienen todos los 45 campos existentes.
-- exported from AWS postgres on session

WITH users AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/replicon/User/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date) FROM read_parquet('s3://{bucket}/raw/replicon/User/**/*.parquet', hive_partitioning = true))
),

user_skills AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/replicon/UserSkills/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date) FROM read_parquet('s3://{bucket}/raw/replicon/UserSkills/**/*.parquet', hive_partitioning = true))
),

first_provider_type AS (
    SELECT
        "User Name"                                                        AS username,
        FIRST_VALUE("Tipo de Proveedor")
            OVER (
                PARTITION BY "User Name"
                ORDER BY     "Skill Name" ASC
                ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
            )                                                              AS tipo_de_proveedor,
        ROW_NUMBER() OVER (PARTITION BY "User Name" ORDER BY "Skill Name" ASC) AS rn
    FROM user_skills
),

provider_type_per_user AS (
    SELECT
        username,
        CAST(tipo_de_proveedor AS VARCHAR) AS tipo_de_proveedor
    FROM first_provider_type
    WHERE rn = 1
)

SELECT
    u.userid,
    u.firstname,
    u.lastname,
    u.username,
    u.loginname,
    u.email,
    u.startdate,
    u.enddate,
    u.externalid,
    u.isenabled,
    u.departmentid,
    u.departmentname,
    u.costcenterid,
    u.costcentername,
    u.divisionid,
    u.divisionname,
    u.locationid,
    u.locationname,
    u.servicecenterid,
    u.servicecentername,
    u.employeetypeid,
    u.employeetypename,
    u.defaultactivityid,
    u.defaultactivityname,
    u.defaultactivitycode,
    u.workweek,
    u.officescheduleid,
    u.officeschedulename,
    u.multifactorauthentication,
    u.currenthourlycostamount,
    u.currenthourlycostcurrencyid,
    u.currenthourlycostcurrencyname,
    u.currenthourlycostcurrencysymbol,
    u.currenthourlybillingamount,
    u.currenthourlybillingcurrencyid,
    u.currenthourlybillingcurrencyname,
    u.currenthourlybillingcurrencysymbol,
    u.currenthourlypayrollamount,
    u.currenthourlypayrollcurrencyid,
    u.currenthourlypayrollcurrencyname,
    u.currenthourlypayrollcurrencysymbol,
    u.currentsupervisoruserid,
    u.currentsupervisorusername,
    u.currentprimaryprojectroleid,
    u.currentprimaryprojectrolename,
    u.load_date,
    pt.tipo_de_proveedor
FROM users u
LEFT JOIN provider_type_per_user pt
       ON u.username = pt.username
ORDER BY u.userid
