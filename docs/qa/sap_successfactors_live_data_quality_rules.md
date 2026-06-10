# Reglas de calidad live SAP SuccessFactors

Reglas que bloquean `GREEN`:

- una fuga cross-tenant o cross-workspace;
- un secreto o token visible en logs, respuestas o reportes;
- PII sensible expuesta indebidamente;
- mutacion sin approval;
- dataset ejecutivo con SQL roto;
- dataset con columna inexistente;
- ruta S3 vieja o de otro cartucho;
- extraccion marcada como exito sin evidencia de run real;
- materializacion marcada como exito sin conteo real;
- Control Room mostrando SuccessFactors mezclado con SAP HCM, Salesforce, HubSpot o Replicon.

Clasificacion por entidad:

- `extracted`: hubo extraccion real y conteo registrado.
- `empty-valid`: la entidad se ejecuto y regreso cero filas sin romper downstream.
- `permission-blocked`: SuccessFactors nego permisos o scope.
- `failed-fixed`: fallo reproducido, corregido y validado en rerun.
- `failed-open`: fallo real pendiente.
- `not-executed`: intento bloqueado o entidad no alcanzada por la corrida.

Los conteos se reconcilian por capas:

SuccessFactors preview/extraccion -> Bronze S3 -> Silver -> Gold -> Postgres/RDS -> Control Room -> Copilot.
