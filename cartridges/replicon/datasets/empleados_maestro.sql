-- empleados_maestro  (master)  cartridge: replicon
-- sources: ["raw/replicon/User", "silver/replicon/replicon_user_latest"]
-- description: Maestro de empleados activos de Replicon con datos de costo, departamento, ubicación, supervisor y fechas de inicio/fin. Derivado de replicon_user_latest (Silver) filtrado a isenabled=true.

SELECT 
  userid AS id_empleado,
  firstname AS nombre,
  lastname AS apellido,
  username AS usuario,
  email,
  departmentname AS departamento,
  locationname AS ubicacion,
  employeetypename AS tipo_empleado,
  CAST(currenthourlycostamount AS DECIMAL(10,2)) AS costo_hora,
  CAST(currenthourlybillingamount AS DECIMAL(10,2)) AS tarifa_facturacion,
  CAST(startdate AS DATE) AS fecha_inicio,
  CAST(enddate AS DATE) AS fecha_fin,
  currentsupervisoruserid AS supervisor_id,
  currentsupervisorusername AS supervisor,
  isenabled AS activo,
  load_date AS fecha_carga
FROM read_parquet('s3://{bucket}/silver/replicon/replicon_user_latest/**/*.parquet')
WHERE isenabled = true
ORDER BY id_empleado
