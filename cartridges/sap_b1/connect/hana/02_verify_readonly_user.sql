-- 02_verify_readonly_user.sql
-- Comprueba que el usuario técnico existe, está activo y SOLO tiene SELECT
-- sobre los esquemas de empresa acordados. Ejecutar en el TENANT con un
-- usuario administrador (bloques A a D) y después con el propio usuario
-- técnico (bloques E y F).
--
-- Los nombres de usuario y esquema van entre comillas simples tal como
-- están en el catálogo: el usuario en mayúsculas si se creó sin comillas.

-- A) Estado del usuario. Esperado: USER_DEACTIVATED = 'FALSE' y
--    PASSWORD_CHANGE_NEEDED = 'FALSE'. INVALID_CONNECT_ATTEMPTS debe ser 0
--    tras la primera conexión correcta de la plataforma.
SELECT USER_NAME, USER_DEACTIVATED, DEACTIVATION_TIME, PASSWORD_CHANGE_NEEDED,
       LAST_SUCCESSFUL_CONNECT, LAST_INVALID_CONNECT_ATTEMPT, INVALID_CONNECT_ATTEMPTS
FROM SYS.USERS
WHERE USER_NAME = '<OMEGA_B1_READER>';

-- B) Privilegios otorgados directamente. Esperado: exactamente una fila por
--    esquema de empresa, con OBJECT_TYPE = 'SCHEMA', PRIVILEGE = 'SELECT' e
--    IS_GRANTABLE = 'FALSE'. Ninguna otra fila.
SELECT GRANTEE, OBJECT_TYPE, SCHEMA_NAME, OBJECT_NAME, PRIVILEGE, IS_GRANTABLE, GRANTOR
FROM SYS.GRANTED_PRIVILEGES
WHERE GRANTEE = '<OMEGA_B1_READER>'
ORDER BY SCHEMA_NAME, PRIVILEGE;

-- C) Roles otorgados. Esperado: solo PUBLIC.
SELECT GRANTEE, ROLE_NAME, GRANTOR
FROM SYS.GRANTED_ROLES
WHERE GRANTEE = '<OMEGA_B1_READER>';

-- D) Privilegios efectivos (directos más heredados por roles), filtrados a
--    lo que importa. Esperado: NINGUNA fila con OBJECT_TYPE = 'SYSTEMPRIVILEGE'
--    y, sobre los esquemas de empresa, únicamente PRIVILEGE = 'SELECT'.
--    SYS.EFFECTIVE_PRIVILEGES exige el filtro por USER_NAME.
SELECT USER_NAME, GRANTEE, GRANTEE_TYPE, OBJECT_TYPE, SCHEMA_NAME, OBJECT_NAME, PRIVILEGE, IS_VALID
FROM SYS.EFFECTIVE_PRIVILEGES
WHERE USER_NAME = '<OMEGA_B1_READER>'
  AND (OBJECT_TYPE = 'SYSTEMPRIVILEGE'
       OR PRIVILEGE IN ('INSERT', 'UPDATE', 'DELETE', 'ALTER', 'DROP', 'EXECUTE', 'CREATE ANY', 'DEBUG')
       OR SCHEMA_NAME IN ('<COMPANY_DB_1>', '<COMPANY_DB_2>', '<COMPANY_DB_3>'))
ORDER BY OBJECT_TYPE, SCHEMA_NAME, PRIVILEGE;

-- E) Prueba de humo CON EL USUARIO TÉCNICO: abra una sesión nueva como
--    <OMEGA_B1_READER> (con hdbsql: -u <OMEGA_B1_READER>, la contraseña se
--    pide en pantalla). Cada consulta debe devolver una fila con la versión
--    de Business One de esa empresa (un entero). Es la misma consulta que la
--    plataforma usa en su prueba de conexión.
SELECT "Version" FROM "<COMPANY_DB_1>"."CINF";
SELECT "Version" FROM "<COMPANY_DB_2>"."CINF";
SELECT "Version" FROM "<COMPANY_DB_3>"."CINF";

-- F) Prueba negativa, también como <OMEGA_B1_READER> (opcional). Debe FALLAR
--    con "insufficient privilege". Si tuviera éxito, el usuario no es de solo
--    lectura: revise los GRANT. No la ejecute con un usuario administrador.
-- UPDATE "<COMPANY_DB_1>"."CINF" SET "Version" = "Version";
