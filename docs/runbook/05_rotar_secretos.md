# 05 — Rotar secretos

> Audiencia: admin que necesita rotar claves por política
> (90 días) o por incidente (sospecha de compromiso). Cubre los tres
> tipos que importan: la Fernet de cifrado en reposo, la
> `INTERNAL_API_KEY` que une los servicios entre sí, y los passwords
> de admin / usuarios.

## ⚠️ Antes de rotar

1. Anuncia ventana de mantenimiento — la rotación de
   `FIELD_ENCRYPTION_KEY` re-cifra todas las `system_settings.value`
   marcadas como secret. **Sin downtime de aplicación, pero la rotación
   misma toca todas las filas con `is_secret=true`** y bloquea
   escrituras concurrentes.
2. Backup primero: ver [06 Backup / restore](06_backup_restore.md).

## 5.1 Rotar `FIELD_ENCRYPTION_KEY` (Fernet)

Sprint v1.39 introdujo soporte multi-key en `cryptography.fernet.MultiFernet`:
la nueva clave queda primero, la vieja queda como fallback para
descifrar lo que aún no se re-cifró.

```bash
# 1. Generar nueva clave
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# 2. Setear en .env como FIELD_ENCRYPTION_KEY_PRIMARY,
#    moviendo la previa a FIELD_ENCRYPTION_KEY_LEGACY
FIELD_ENCRYPTION_KEY_PRIMARY=<NUEVA>
FIELD_ENCRYPTION_KEY_LEGACY=<VIEJA>     # mantener temporalmente

# 3. Restart la consola (sólo console — el cifrado vive ahí)
docker compose restart console

# 4. Re-cifrar todas las filas con la nueva clave:
docker exec -it mode_console python -m app.maintenance.rotate_fernet --confirm

# 5. Verificar
docker exec mode_postgres psql -U postgres modecissions -c \
  "SELECT COUNT(*) FROM system_settings WHERE is_secret = true;"
# Todas deben seguir descifrándose tras el restart final.

# 6. Borrar la LEGACY de .env y reiniciar
unset FIELD_ENCRYPTION_KEY_LEGACY        # de tu shell
# editar .env, quitar la línea LEGACY
docker compose restart console
```

> El bloque `MultiFernet` ya está implementado en `console/app/services/crypto.py`
> y probado en `tests/test_vault_fernet_rotation.py`.

## 5.2 Rotar `INTERNAL_API_KEY`

Esta clave une console ↔ cartridges ↔ refinement ↔ vault ↔ mcp-infra.
Rotarla requiere **reiniciar el stack entero**, no hay rolling restart.

```bash
# 1. Generar
openssl rand -hex 32

# 2. Setear nuevo valor en .env: INTERNAL_API_KEY=<nuevo>

# 3. Restart total
docker compose -f infra/docker-compose.yml --profile sap up -d --build

# 4. Smoke
make smoke   # debe seguir 30/30
```

> No hay grace period: viejo y nuevo no conviven. Si el smoke falla a
> los 30s, revierte `.env` y haz `up -d` de nuevo.

## 5.3 Rotar password del admin (o de cualquier usuario)

Vía UI: el usuario va a `/me` → "Cambiar password".

Vía CLI (para admin de servicio):

```bash
docker exec -it mode_console python -c "
from app.services.auth import set_password
import asyncio
asyncio.run(set_password(email='admin@example.com', new_password='${NEW_PASSWORD}'))
"
```

Verificar en `audit_events`:

```sql
SELECT created_at, action, ip, user_agent
FROM audit_events
WHERE action='user.password_changed' ORDER BY created_at DESC LIMIT 1;
```

## 5.4 Rotar credenciales DB (omega_* roles)

Patrón Sprint v1.38: cada servicio usa su propio role least-privilege.

```sql
-- Como postgres superuser
ALTER ROLE omega_console PASSWORD '<nuevo>';
```

Update `.env`: `OMEGA_CONSOLE_PASSWORD=<nuevo>` y restart **sólo** el
servicio que usa ese role:

```bash
docker compose restart console
```

Repetir por servicio. El smoke 30/30 confirma que el handshake DB
funciona.

## Si algo falla

- **Tras rotar Fernet, algunas filas no descifran**: el script
  `rotate_fernet` no terminó. Restaurar de backup y reintentar con
  más memoria.
- **Tras rotar `INTERNAL_API_KEY`, los cartuchos devuelven 401 al
  console**: alguno tiene cacheado el viejo valor — `docker compose
  restart <servicio>`.
