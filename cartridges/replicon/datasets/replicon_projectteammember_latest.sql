-- replicon_projectteammember_latest  (silver)  cartridge: replicon
-- sources: ["raw/replicon/ProjectTeamMember"]
-- description: ProjectTeamMember - Ultima extraccion Replicon Bronze

SELECT * FROM read_parquet('s3://{bucket}/raw/replicon/ProjectTeamMember/**/*.parquet', hive_partitioning=true, union_by_name=true)
