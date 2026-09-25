# 06 — Backup y restore

> Audiencia: SRE que mantiene la plataforma. Backups deben correr
> al menos diariamente; antes de cualquier rotación de secretos
> o upgrade de versión, hacer uno manual.

## Qué hay que respaldar

| Componente | Ubicación | Sensibilidad |
|---|---|---|
| Postgres `modecissions` | volumen `postgres_data` | **alta** — users, audit_events |
| Postgres `airflow`      | mismo volumen           | media — DAG run history |
| Postgres `superset`     | mismo volumen           | baja — dashboards y conexiones (no datos) |
| Postgres `postgres_gold`| volumen `postgres_gold_data` | media — gold layer materializada |
| MinIO buckets           | volumen del contenedor minio | **alta** — bronze parquet |
| `.env`                  | host                    | **alta** — secretos |

## 6.1 Backup Postgres (todas las DBs)

```bash
mkdir -p /var/backups/omega/$(date +%F)
cd /var/backups/omega/$(date +%F)

docker exec mode_postgres pg_dumpall -U postgres \
  --clean --if-exists > all.sql

# Comprimir; opcionalmente cifrar con gpg
gzip --best all.sql
```

Tamaño esperado tras compresión: ~50 MB para 1M filas operacionales.

## 6.2 Backup MinIO

```bash
docker run --rm \
  --network=infra_default \
  -v /var/backups/omega/$(date +%F)/minio:/dump \
  ghcr.io/emmanuelnavaromero02-commits/omega-mc:RELEASE.2024-11-21T17-21-54Z sh -c "
    mc alias set src http://minio:9000 minio \$MINIO_SECRET_KEY &&
    mc mirror --overwrite src /dump
  "
```

Por bucket, no por servicio: `lakehouse/bronze/...`, `lakehouse/silver/...`.

## 6.3 Backup `.env`

```bash
cp /opt/omega/.env /var/backups/omega/$(date +%F)/env.encrypted
gpg --symmetric --cipher-algo AES256 /var/backups/omega/$(date +%F)/env.encrypted
shred -u /var/backups/omega/$(date +%F)/env.encrypted   # plaintext ya cifrado
```

Guarda la passphrase en un gestor (1Password, vault corporativo).

## 6.4 Restore (DR completo)

Pre-condición: máquina limpia, Docker instalado, repo checked-out
en `/opt/omega`.

```bash
# 1. .env de vuelta
gpg --decrypt env.encrypted.gpg > /opt/omega/.env

# 2. Postgres
docker compose -f infra/docker-compose.yml up -d postgres
sleep 10                                 # esperar healthcheck
gunzip -c all.sql.gz | docker exec -i mode_postgres psql -U postgres

# 3. MinIO
docker compose -f infra/docker-compose.yml up -d minio
sleep 5
docker run --rm --network=infra_default \
  -v /var/backups/omega/<FECHA>/minio:/dump \
  ghcr.io/emmanuelnavaromero02-commits/omega-mc:RELEASE.2024-11-21T17-21-54Z sh -c "
    mc alias set tgt http://minio:9000 minio \$MINIO_SECRET_KEY &&
    mc mirror --overwrite /dump tgt
  "

# 4. Resto del stack
docker compose -f infra/docker-compose.yml --profile sap up -d --build

# 5. Validar
make smoke                                # debe ser 30/30
```

## Verificación periódica

Mensualmente: levanta un stack secundario desde el último backup en una
VM aparte y corre `make smoke` ahí. Sin esto, no sabes si el backup
realmente funciona.

## Si algo falla en restore

- **`pg_dumpall` con errores `role does not exist`**: el dump trae
  `CREATE ROLE` para los `omega_*`. Está bien — esos roles los crean
  los scripts en `infra/init/`. Si el dump corre antes que esos
  scripts, hay race. Ejecuta primero los scripts SQL y luego el
  pg_dumpall.
- **MinIO `mc mirror` con `access denied`**: `MINIO_SECRET_KEY` en
  `.env` no es la que se usó al backup. Imposible de recuperar sin la
  clave original.

## Actualización a v1.43.2+ — MinIO data migration

A partir de v1.43.2, MinIO está pinned a
`RELEASE.2024-12-18T13-15-44Z` (`infra/docker-compose.yml`). Si tu
data local en `data/lakehouse/` fue creada con una versión más
reciente (por ejemplo `minio:latest` descargada antes de que el pin
entrara en main), al levantar el stack verás este error:

```
FATAL Unable to initialize backend: decodeXLHeaders:
      Unknown xl meta version 3
```

El backend de MinIO escribe metadatos (`xl.meta` v1, v2, v3, …) que
versiones anteriores no saben leer. Esto NO es un bug del pin — es
la semántica documentada de MinIO al hacer downgrade.

### Solución (recomendada para entornos de desarrollo locales)

1. Apaga el stack y borra los volúmenes locales. No uses esto en AWS,
   staging, producción ni en hosts con datos de cliente:

   ```bash
   make nuke CONFIRM=NUKE NUKE_SCOPE=local-dev
   ```

2. Mueve la data vieja a un backup (NO la borres por si necesitas
   leerla más adelante):

   ```bash
   mv data/lakehouse data/lakehouse.pre-v1432-backup-$(date +%Y%m%d-%H%M%S)
   ```

3. Levanta el stack normalmente:

   ```bash
   make up
   ```

4. Re-extrae los datos que necesites desde los cartridges. La data
   *raw* sigue en los sistemas origen (SAP, Replicon); el lakehouse
   es la copia derivada, no la verdad.

### Si necesitas recuperar el lakehouse viejo

Si tienes contenido en `data/lakehouse.pre-v1432-backup-*` que vale
la pena rescatar, levanta MinIO temporalmente con una versión más
nueva, exporta los buckets con `mc mirror`, y vuelve al pin oficial.
**No** dejes el pin "fuera de banda" — la versión pinned es la única
combinación verificada por el smoke test.

```yaml
# infra/docker-compose.yml — TEMPORAL, revertir después de exportar
image: ghcr.io/emmanuelnavaromero02-commits/omega-minio:RELEASE.2025-XX-XX...   # rebuilt from source with mirror-minio.yml first
```

Mientras la versión temporal está corriendo, exporta los buckets a
un directorio fuera del volumen Docker:

```bash
mc alias set src http://localhost:9000 minio "$MINIO_SECRET_KEY"
mc mirror --overwrite src/lakehouse /tmp/lakehouse-export
```

Después de exportar, vuelve al pin oficial y restaura:

```bash
make nuke CONFIRM=NUKE NUKE_SCOPE=local-dev
git checkout infra/docker-compose.yml    # vuelve al pin oficial
make up
mc alias set tgt http://localhost:9000 minio "$MINIO_SECRET_KEY"
mc mirror --overwrite /tmp/lakehouse-export tgt/lakehouse
```

## v1.43.3 — Fix automático de permisos SAP (migración 46)

A partir de v1.43.3, la migración `46_sap_jobs_permissions.sql`
corrige automáticamente la regresión que requería parchear permisos
SAP a mano en v1.43.2:

  * `GRANT CREATE ON SCHEMA public` a los 4 roles cartridge
    (`sap_hcm`, `sap_s4`, `sap_sf`, `replicon`).
  * Crea el rol compartido `omega_cartridge_jobs_owner` (NOLOGIN) y
    transfiere ownership de `jobs` + `idx_jobs_status`.
  * Otorga membresía de `omega_cartridge_jobs_owner` a los 4 roles.

Si tu DB ya tiene los permisos parcheados manualmente (por ejemplo
desde un `docker compose up` previo donde corriste los GRANT a
mano), **la migración es idempotente** y detecta los estados ya
correctos sin volver a aplicar los `ALTER`. No hay que hacer nada
especial; los SAP cartridges arrancan healthy sin intervención
manual tras un `docker compose down -v && up` limpio.

Para validar a posteriori:

```bash
docker exec mode_postgres psql -U postgres -d modecissions \
  -c "SELECT filename FROM schema_migrations WHERE filename LIKE '46_%';"
# → debe devolver 1 fila

docker exec mode_postgres psql -U postgres -d modecissions \
  -c "\\d jobs" | head -5
# → Owner: omega_cartridge_jobs_owner
```
