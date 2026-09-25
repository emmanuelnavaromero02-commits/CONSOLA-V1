-- 03_revoke_readonly_user.sql
-- Revoca el acceso y elimina el usuario técnico de la plataforma. Ejecutar en
-- el TENANT con un usuario administrador (USER ADMIN).
--
-- Cuándo usarlo: para cortar el acceso de la plataforma de inmediato, o como
-- primer paso de una rotación de credenciales (03 y después 01 con una
-- contraseña nueva).
--
-- Efecto: DROP USER cierra las sesiones abiertas del usuario. La siguiente
-- extracción de la plataforma falla con error de autenticación y queda
-- registrada como fallida (nunca como "0 filas"), así que el corte es
-- visible desde los dos lados.

-- 1) Revocar el SELECT de cada esquema. DROP USER lo haría de forma
--    implícita; hacerlo explícito deja constancia en la auditoría de HANA.
REVOKE SELECT ON SCHEMA "<COMPANY_DB_1>" FROM <OMEGA_B1_READER>;
REVOKE SELECT ON SCHEMA "<COMPANY_DB_2>" FROM <OMEGA_B1_READER>;
REVOKE SELECT ON SCHEMA "<COMPANY_DB_3>" FROM <OMEGA_B1_READER>;
-- Solo si en 01 se otorgó el esquema común:
-- REVOKE SELECT ON SCHEMA "SBOCOMMON" FROM <OMEGA_B1_READER>;

-- 2) Eliminar el usuario. CASCADE elimina también los objetos que el usuario
--    pudiera poseer; no debería tener ninguno, nunca tuvo permiso de crear.
DROP USER <OMEGA_B1_READER> CASCADE;

-- Alternativa reversible, si prefiere suspender sin borrar (por ejemplo
-- durante una revisión): desactivar y, más adelante, reactivar.
-- ALTER USER <OMEGA_B1_READER> DEACTIVATE USER NOW;
-- ALTER USER <OMEGA_B1_READER> ACTIVATE USER NOW;
