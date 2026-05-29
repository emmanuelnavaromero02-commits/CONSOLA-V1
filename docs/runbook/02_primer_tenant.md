# 02 — Crear el primer tenant (workspace) e invitar al primer usuario

> Audiencia: admin nuevo que acaba de seguir
> [01 Arrancar desde cero](01_arrancar_desde_cero.md) y necesita crear
> el primer workspace para empezar a operar.

## Pre-requisitos

- Stack OMEGA arriba y healthy (`docker compose ps`).
- Credenciales de bootstrap admin creadas manualmente con
  `console/app/bootstrap_admin.py` después de que `console` esté healthy.
- MailHog (mail capturador local) accesible en
  http://localhost:8025 — los mails de invitación caen ahí.

## Pasos

### 1. Crear y entrar como bootstrap admin

Ejecuta el bootstrap manual con un password fuerte de un solo uso:

```bash
docker compose -f infra/docker-compose.yml exec \
  -e BOOTSTRAP_ADMIN_PASSWORD='<password-temporal-fuerte>' \
  -e BOOTSTRAP_ADMIN_FULL_NAME='System Administrator' \
  console python -m app.bootstrap_admin admin@your-domain.test
```

Abre http://localhost:8000/login e ingresa con el email y password
usados en ese comando. Cambia el password después del primer login.

Verificación: te lleva al home con la cabecera mostrando el rol
**admin**.

### 2. Crear el workspace

Ir a **`/iam`** (icono Identity en el menú lateral). Sección
"Workspaces" → botón "Crear workspace".

Campos:

| Campo | Valor sugerido |
|---|---|
| Nombre | `acme-prod` |
| Slug | `acme-prod` (auto-derivado) |
| Descripción | "Producción ACME — datos reales" |

Submit. Aparece en la lista con UUID nuevo.

### 3. Invitar al primer usuario

En la misma página, sección "Usuarios" → "Invitar".

| Campo | Valor |
|---|---|
| Email | `consultor@acme.com` |
| Workspace | `acme-prod` |
| Rol | `workspace_admin` |

Submit. El correo de invitación se imprime en MailHog (http://localhost:8025) en lugar de salir a Internet.

### 4. El usuario activa la cuenta

El usuario abre el link del mail (algo como `http://localhost:8000/activate?token=...`)
y elige password. Lo dejará logged-in en su workspace.

## Verificación

```bash
docker exec mode_postgres psql -U postgres modecissions -c \
  "SELECT u.email, w.name, m.role FROM users u
     JOIN workspace_members m ON m.user_id = u.id
     JOIN workspaces w ON w.id = m.workspace_id
     WHERE u.email = 'consultor@acme.com';"
```

Debe devolver una fila.

```bash
docker exec mode_postgres psql -U postgres modecissions -c \
  "SELECT action, ip, user_agent, created_at FROM audit_events
     WHERE action LIKE 'user.%' ORDER BY created_at DESC LIMIT 5;"
```

Verás `user.invited` con `ip` y `user_agent` poblados (v1.41.0
forensic-complete).

## Si algo falla

- **El mail no aparece en MailHog**: revisa `docker compose logs console`
  buscando `smtp` — probable que `SMTP_HOST` no esté apuntando a `mailhog`.
- **El link de activación dice "token inválido"**: el token caduca a las
  72h. Re-invita.
- **Login del usuario nuevo devuelve 403**: revisa que el workspace_member
  existe en la query anterior. Sin membership = no acceso.
