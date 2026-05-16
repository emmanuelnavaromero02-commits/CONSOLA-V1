# 06 — Backup y restore

> Audiencia: SRE que mantiene la plataforma. Backups deben correr
> al menos diariamente; antes de cualquier rotación de secretos
> (ver [05](05_rotar_secretos.md)) o upgrade de versión, hacer uno
> manual.

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
  minio/mc:RELEASE.2025-01-17T16-25-43Z sh -c "
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
  minio/mc:RELEASE.2025-01-17T16-25-43Z sh -c "
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
