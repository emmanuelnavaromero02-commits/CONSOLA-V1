# Matriz de cobertura AWS live SAP SuccessFactors

La matriz generada por `make sap-successfactors-aws-live-max` vive en:

`docs/release-evidence/sap_successfactors_aws_live/<run_id>/coverage_matrix.md`

Columnas obligatorias:

| Campo | Descripcion |
|---|---|
| entidad | Entidad SuccessFactors o dataset ejecutivo derivado. |
| endpoint OData | Endpoint conceptual OData real usado por el cartucho. |
| preview real | Si hubo lectura real de datos o metadata. |
| extraccion real | Si la entidad paso por extraccion live. |
| filas extraidas | Conteo registrado sin exponer PII. |
| estado | `extracted`, `empty-valid`, `permission-blocked`, `failed-fixed`, `failed-open`, `not-executed`. |
| run_id / batch_id | Identificadores reales registrados por la corrida. |
| Bronze path | Ruta S3 real de salida. |
| Silver dataset | Dataset derivado esperado. |
| Gold dataset | Dataset ejecutivo esperado. |
| Postgres registry | Evidencia de registro en RDS/Postgres. |
| Control Room/App/KB/Agent/Copilot | Visibilidad o bloqueo honesto por superficie. |
| errores | Error exacto redactado. |
| fix aplicado | Cambio del repo aplicado en la vuelta, si existio. |

Un `PASS` de carga no sustituye esta matriz. Si una entidad no se ejecuto o no tiene permiso, debe quedar visible.
