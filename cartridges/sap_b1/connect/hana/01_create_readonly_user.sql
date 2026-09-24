-- 01_create_readonly_user.sql
-- Crea el usuario técnico de SOLO LECTURA con el que la plataforma lee las
-- bases de empresa de SAP Business One sobre SAP HANA 2.0.
--
-- Dónde ejecutarlo: conectado al TENANT donde viven las empresas (no en
-- SYSTEMDB), con un usuario que tenga el privilegio USER ADMIN (por ejemplo
-- SYSTEM). Herramientas: HANA Studio, HANA Cockpit (SQL Console) o hdbsql.
--
-- Qué sustituir antes de ejecutar (busque los marcadores <...>):
--   <OMEGA_B1_READER>   nombre del usuario técnico. Sugerencia: OMEGA_B1_READER.
--                       Sin comillas, HANA lo guarda en mayúsculas.
--   <PASSWORD_...>      contraseña generada por su TI (mínimo 8 caracteres con
--                       mayúsculas, minúsculas y dígitos, o lo que exija su
--                       política de contraseñas). Se entrega a la plataforma
--                       por un canal seguro (gestor de contraseñas o similar),
--                       nunca por correo, y se guarda cifrada en el vault.
--   <COMPANY_DB_1..3>   nombres exactos de los tres esquemas de empresa del
--                       piloto (en Business One suelen empezar por SBO_).
--                       Distinguen mayúsculas y minúsculas: van entre comillas.
--
-- Qué NO hace este script: no otorga privilegios de sistema, no otorga roles,
-- no da acceso a otros esquemas ni permisos de escritura. El usuario queda
-- únicamente con el rol PUBLIC, que HANA asigna a todo usuario y no se puede
-- retirar: da acceso a vistas públicas del catálogo, no a datos de empresa.

-- 1) Crear el usuario. NO FORCE_FIRST_PASSWORD_CHANGE evita que HANA exija
--    cambiar la contraseña en el primer inicio de sesión, algo que un proceso
--    automático no puede hacer.
CREATE USER <OMEGA_B1_READER> PASSWORD "<PASSWORD_GENERATED_BY_CUSTOMER_IT>" NO FORCE_FIRST_PASSWORD_CHANGE;

-- 2) Desactivar la caducidad de la contraseña para este usuario. Con la
--    política por defecto (182 días) la extracción dejaría de funcionar sin
--    aviso. La rotación se hace de forma planificada (03 y después 01 con
--    contraseña nueva), no por caducidad.
ALTER USER <OMEGA_B1_READER> DISABLE PASSWORD LIFETIME;

-- 3) Permiso de lectura (SELECT) sobre cada esquema de empresa del piloto.
--    Sin WITH GRANT OPTION: el usuario no puede ceder el permiso a nadie.
GRANT SELECT ON SCHEMA "<COMPANY_DB_1>" TO <OMEGA_B1_READER>;
GRANT SELECT ON SCHEMA "<COMPANY_DB_2>" TO <OMEGA_B1_READER>;
GRANT SELECT ON SCHEMA "<COMPANY_DB_3>" TO <OMEGA_B1_READER>;

-- 4) OPCIONAL. SBOCOMMON es el esquema común de Business One (licencias,
--    versiones, lista de empresas). La plataforma NO lo necesita hoy; queda
--    documentado por si en el futuro hiciera falta leer, por ejemplo, la
--    tabla de versiones. Descomentar solo si se acuerda expresamente.
-- GRANT SELECT ON SCHEMA "SBOCOMMON" TO <OMEGA_B1_READER>;

-- Alternativa más estricta, a criterio de su TI: en lugar de SELECT sobre el
-- esquema completo, SELECT tabla por tabla. Las 45 tablas que lee la
-- plataforma son:
--   CINF OADM OCRN ORTT OACT OFPR OPRC OCRG OSLP OWHS OITB OITW OBTN OBTQ OIBT
--   OCRD OITM OITT ITT1 OINV INV1 ORIN RIN1 ODLN DLN1 ORDN RDN1 ORDR RDR1
--   OPCH PCH1 ORPC RPC1 OPDN PDN1 OPOR POR1 OJDT JDT1 OWOR WOR1 OINM IBT1
--   OWTR WTR1
-- Ejemplo de una línea (repetir por tabla y por esquema):
--   GRANT SELECT ON "<COMPANY_DB_1>"."OINV" TO <OMEGA_B1_READER>;
-- Con esta variante, cada tabla nueva en el catálogo de la plataforma requiere
-- un GRANT nuevo de su lado.
