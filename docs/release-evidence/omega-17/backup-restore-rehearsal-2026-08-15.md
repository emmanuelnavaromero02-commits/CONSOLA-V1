# OMEGA-17 · F4-1 — Backup → Restore rehearsal (aislado, esquema real)

- **Fecha**: 2026-08-15
- **Base**: `main@b6c63738` (v1.45.222-beta + F3), rama `claude/f4-backup-restore`
- **Resultado**: **PASS** — backup válido → restore válido con fidelidad byte-a-byte en las tablas verificadas.

## Entorno del rehearsal

Aislamiento total, sin tocar ningún entorno vivo:

- **Origen**: dos Postgres efímeros con el **esquema real** — operacional `pgvector/pgvector:pg15` con los 203 scripts de `infra/init` montados en `docker-entrypoint-initdb.d` (mismo mecanismo del compose; 0 errores de init) y gold `postgres:15 -p 5433` con `infra/init_gold` (0 errores; requiere contraseñas de rol **distintas** entre publisher/verifier/refinement o el init 39 aborta — comportamiento correcto verificado de paso).
- **Siembra**: operacional — 2 tenants + 2 workspaces + 2 `entity_watermarks` scoped + 1 alias SF; gold — tabla runtime `gold_dr_rehearsal_probe` con 3 filas (propiedad importante: el backup debe transportar tablas creadas en runtime, no solo las de init).
- **Backup**: `infra/terraform/deploy/backup.sh` real, ejecutado con `BACKUP_STORAGE_BACKEND=local` + `OMEGA_DR_ALLOW_NON_ROOT=1` desde un DEPLOY_DIR aislado con servicios compose `postgres`/`postgres_gold` (mismos nombres que el host de deploy). `pg_dumpall --clean --if-exists | gzip` ×2 + manifest sha256, sin cambios en el camino de dump.
- **Restore**: `infra/terraform/deploy/restore.sh` **verbatim** (backend `local`, `CONFIRM_RESTORE=modecissions`, `RUN_SMOKE=0`) contra un segundo DEPLOY_DIR aislado con instancias **vírgenes** (sin init) `pgvector:pg15` y `postgres:15 -p 5433`. Exit 0, **cero** líneas `ERROR` en 3.384 líneas de log.

## Hallazgo del rehearsal (bug real, corregido)

`pg_dumpall --clean` emite `DROP ROLE IF EXISTS postgres;` y `CREATE ROLE postgres;` para el rol bootstrap. `DROP ROLE` del usuario conectado falla siempre (`current user cannot be dropped`) y con `ON_ERROR_STOP=1` **aborta el restore completo a mitad** (exit 3, tras haber dropeado las bases): el `restore.sh` publicado **no podía completar ningún restore**, en ningún cluster. Fix en `restore.sh`: `strip_bootstrap_role` filtra exactamente esas sentencias y **solo dentro del preámbulo global** (antes del primer `\connect`), de modo que ninguna fila de datos `COPY` pueda coincidir con el filtro; todo otro error sigue deteniendo el restore. Evidencia: primer intento sin filtro → `PG_RESTORE_EXIT=3` / `GOLD_RESTORE_EXIT=3`; con el fix → exit 0 sin errores.

Nota de runbook: un restore repone también los hashes de contraseña de los roles al estado del backup (comportamiento inherente a `pg_dumpall`); si se rotaron contraseñas de servicio después del backup, re-sincronizar `.env`/Secret Manager tras restaurar.

## Integridad y fidelidad

`sha256` recomputado de ambos dumps == manifest (`INTEGRITY PASS`):

| Artefacto | sha256 | bytes |
|---|---|---|
| `postgres.sql.gz` | `d4b7962cf35c67f91bbd0fe31de3493895ee9b66929b825bff1289e4e86b7c0c` | 175 149 |
| `postgres_gold.sql.gz` | `17749f07016a9a1ce54d0fa299b79d55441414f2af0c11de3e95a51d746de0be` | 16 529 |

Verificación fuente vs restaurado (conteo + `md5(string_agg(fila::text))` con orden determinista) — **diff vacío, IDÉNTICO**:

| Tabla | Filas | md5 (igual en fuente y restaurado) |
|---|---|---|
| `tenants` | 3 | `41a9f1b75a338415a8aec0a6e530d042` |
| `workspaces` | 3 | `813a9dfc2d45d84e526f1e1f17561420` |
| `entity_watermarks` | 2 | `137423fdc1d280173c10e14405f23d36` |
| `sap_successfactors_tenant_entity_aliases` | 1 | `be6de35ba9c620d5794bb27affff7859` |
| `schema_migrations` (operacional) | 177 | `5c0ac235b10e539d7fc4ee0e1426e19d` |
| `gold_dr_rehearsal_probe` (runtime) | 3 | `d6ae9e76fbe1e93eb3c35436b7af6e66` |
| `schema_migrations` (gold) | 9 | `2c840620c208fe5053713f8dcebcc34a` |
| `omega_publication.dataset_publication_heads` | 0 | n/a |

Además: las **7 políticas RLS** de `entity_watermarks` + `sap_successfactors_tenant_entity_aliases` existen en la instancia restaurada (el restore preserva el modelo de tenancy). Manifest completo: [`dr-rehearsal-local-20260815.manifest.json`](dr-rehearsal-local-20260815.manifest.json).

## Generalización de backup.sh + restore.sh (S3 + GCS + local)

- Backend por env en **ambos** scripts: `BACKUP_STORAGE_BACKEND` explícito, o inferido `OMEGA_BACKUP_LOCAL_DIR` (rehearsal) > `GCS_BUCKET` (hosts GCP canónicos, ya exportado por terraform-gcp) > `S3_BUCKET_NAME` (AWS); el backend elegido se anuncia en el log (`[backup] storage backend: …`).
- GCS usa `gcloud storage cp` / `du` / `rsync` (con `--delete-unmatched-destination-objects` para el modo delete del restore); los comandos AWS del camino S3 quedaron intactos y los controles estáticos de `make dr-rehearsal` (`pg_dumpall…`, `manifest.json`, `aws s3 sync`, `CONFIRM_RESTORE`, `RESTORE_DELETE_PREFIX`, `backups/*`) siguen en verde.
- Manifest: los campos `storage_backend`/`gcs_bucket`/`local_dir` se emiten **solo** en backends no-S3, de modo que el manifest S3 (bytes subidos y línea `OMEGA_BACKUP_MANIFEST`) queda byte-idéntico al de antes.
- Probado: backend `gcs` sin `GCS_BUCKET` falla con mensaje claro; `gcloud` presente en el host de trabajo (SDK 575.0.1).

## Revisión adversarial del diff (3 lentes, pre-commit)

Hallazgos aplicados: (1) `restore.sh` era S3-only — sin simetría de backend un backup GCS/local era irrestaurable por los scripts del repo → generalizado; (2) el filtro del rol bootstrap corría sobre todo el stream (riesgo teórico de descartar filas `COPY` byte-idénticas a las sentencias) → acotado al preámbulo global; (3) el manifest S3 dejaba de ser byte-idéntico → campos nuevos gated a no-S3; (4) el inventario S3 (`grep -v '/backups/'`) nunca excluía el prefijo top-level `backups/` — bug preexistente — → ancla `^backups/`; (5) parsing del `gcloud storage du` robusto a espacios/tamaños grandes (index/substr, sin regex sobre el prefijo); (6) errores de `gcloud` ya no se silencian con `2>/dev/null`; (7) `OMEGA_BACKUP_MANIFEST` se construye con asignación para que `set -e` capture fallos de canonicalización.

## F4-2 — Backup programado (mecanismo commiteado)

- **Cron versionado**: [`infra/terraform-gcp/files/omega-backup.cron`](../../../infra/terraform-gcp/files/omega-backup.cron) — diario 03:10 UTC como root, `BACKUP_STORAGE_BACKEND=gcs` pineado (una variable S3 ambiental no puede desviar el destino), log a `/var/log/omega-backup.log`.
- **Cableado en provisioning**: `startup.sh.tftpl` instala el cron (`install -m 0644` → `/etc/cron.d/omega-backup`) en cada VM aprovisionada — el mecanismo viaja con el release.
- **Soporte de layout GCP en `backup.sh`**: `BACKUP_ENV_FILE` (el host GCP tiene el env en `infra/.env`) y `BACKUP_COMPOSE_FILES` (compose `infra/docker-compose.yml` + overlay gcp) — defaults intactos para AWS.
- **Validación local**: contrato estático `tests/test_gcp_backup_cron_contract.py` (5 passed: forma del archivo, schedule diario válido, comando con backend gcs + logging, provisioning instala el cron, backup.sh soporta la invocación) + corrida E2E real de `backup.sh` con la **forma exacta de invocación del cron** (overrides de env/compose, layout GCP emulado) contra Postgres efímero → backup completo con los 5 artefactos.
- **Nota (diferido a F6)**: la unificación del migrador AWS al endurecido queda para F6 (decomisión de AWS); el camino canónico GCP ya usa el migrador endurecido, así que "migrador único guardado" está cubierto para producción.

## BLOCKED — requiere acceso GCP del owner (NO hecho)

La programación del backup en el GCP vivo y el restore de producción real quedan pendientes del owner:

1. **Activar el cron en la VM GCP ya aprovisionada** (una vez; las VMs nuevas lo reciben del provisioning):
   ```bash
   gcloud compute ssh <VM_NAME> --tunnel-through-iap --project <PROJECT>   # nombres: terraform -chdir=infra/terraform-gcp output
   sudo install -m 0644 -o root -g root \
     /opt/modecissions/current/infra/terraform-gcp/files/omega-backup.cron \
     /etc/cron.d/omega-backup
   ```
2. **Primera corrida manual + verificación**: ejecutar el mismo comando sin cron y comprobar `gcloud storage ls gs://<LAKEHOUSE_BUCKET>/backups/` + el `OMEGA_BACKUP_MANIFEST` del log.
3. **Rehearsal de restore contra datos reales**: en staging desechable (nunca prod directo), `OMEGA_DR_REHEARSAL_EXECUTE=1 make dr-rehearsal` con el `BACKUP_ID` recién creado.

Sin estos pasos ejecutados por el owner, el respaldo en la nube viva **no existe** — este documento solo prueba que el mecanismo funciona.
