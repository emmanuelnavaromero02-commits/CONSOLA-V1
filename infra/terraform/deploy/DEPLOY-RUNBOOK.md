# MODecissions — Deploy Runbook (AWS)

Guía operacional end-to-end para desplegar MODecissions desde una laptop Windows nueva.

**Arquitectura objetivo**

| Componente   | Tipo / Spec                | Función                                  |
|--------------|----------------------------|------------------------------------------|
| EC2 App      | `m6i.xlarge`               | Docker Compose, build local, repo clonado |
| EC2 VPN      | `t3.nano`                  | WireGuard (wg-easy) en Docker             |
| Postgres     | Contenedores en EC2 App (`pgvector/pgvector:pg15` + `postgres:15`) | 4 databases (modecissions, _gold, superset, airflow) — paridad con local |
| S3           | Bucket privado             | Lakehouse (reemplaza MinIO)               |

**Servicios docker en EC2 App**:

| Servicio              | Puerto | Rol                                                         |
|-----------------------|--------|-------------------------------------------------------------|
| console               | 8000   | Público solo vía ALB HTTPS; target privado de EC2 App       |
| workspace             | 8001   | Público solo vía ALB HTTPS; target privado de EC2 App       |
| refinement            | 8500   | Interno/VPN: DuckDB + LLM SQL para datasets                 |
| mcp-infra             | 8010   | Interno/VPN: MCP tools para Airflow, Postgres, Superset, RAG, cartridges |
| superset              | 8088   | Interno/VPN: BI tradicional                                 |
| airflow               | 8082   | Interno/VPN: orquestación de DAGs                           |
| mailhog (UI)          | 8025   | Interno/VPN: Dev SMTP catcher (placeholder hasta SES)       |
| mailhog (SMTP)        | 1025   | SMTP interno (consumido por console)                        |
| postgres              | 5432*  | DBs modecissions, superset, airflow                         |
| postgres_gold         | 5433*  | DB modecissions_gold (master + gold layer)                  |

\* Postgres ports no están expuestos al host — solo en la red `modecissions_net` interna.

**Acceso**: console y workspace salen por ALB público HTTPS. Airflow, Superset, Postgres, MinIO/S3, MailHog, MCP, cartuchos y puertos directos quedan detrás de VPN/SSM. SSH público queda cerrado por defecto; WireGuard `51820/udp` es el único puerto VPN público esperado.

**Tiempo total estimado**: ~2 horas (de las cuales ~30-45 min son builds desatendidos).

---

## 0. Prerequisitos y setup inicial — 20 min

Herramientas requeridas en Windows.

| Herramienta        | Versión mín | Link                                                                     |
|--------------------|-------------|--------------------------------------------------------------------------|
| AWS CLI v2         | 2.15+       | https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html |
| Terraform          | 1.6+        | https://developer.hashicorp.com/terraform/downloads                       |
| WireGuard          | latest      | https://www.wireguard.com/install/                                        |
| Git                | 2.40+       | https://git-scm.com/download/win                                          |
| PowerShell         | 7.4+        | https://github.com/PowerShell/PowerShell/releases                         |
| OpenSSH (`ssh-keygen`) | included | solo para generar deploy key — `Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0` |

**Comandos de verificación** (correr todos en una sola PowerShell):

```powershell
aws --version              # aws-cli/2.15.x
terraform version          # Terraform v1.6+
git --version              # git version 2.40+
$PSVersionTable.PSVersion  # 7.4.x
ssh-keygen --help 2>&1 | Select-Object -First 1
Test-Path "C:\Program Files\WireGuard\wireguard.exe"   # True
```

**Configurar credenciales AWS**:

```powershell
aws configure
# AWS Access Key ID:     <de tu IAM user>
# AWS Secret Access Key: <de tu IAM user>
# Default region:        us-east-1
# Default output format: json

aws sts get-caller-identity   # debe devolver tu Account/UserId/Arn
```

---

## 1. Deploy Key en GitHub — 5 min

La EC2 App necesita poder hacer `git clone/pull` del repo privado. Generamos un keypair dedicado y registramos solo la **pública** en GitHub como Deploy Key.

```powershell
# Desde la raíz del repo local (ej: C:\temp\eumx\modecissions\)
ssh-keygen -t ed25519 -C "modecissions-deploy" -f .\modecissions-deploy-key -N '""'

# Verifica
ls .\modecissions-deploy-key*
# modecissions-deploy-key       (privada — NO subir a Git)
# modecissions-deploy-key.pub   (pública — la que pegamos en GitHub)

Get-Content .\modecissions-deploy-key.pub
```

**En GitHub**:

1. Repo → **Settings** → **Deploy keys** → **Add deploy key**
2. Title: `modecissions-ec2-app`
3. Key: pegar el contenido completo de `modecissions-deploy-key.pub`
4. **NO** marcar "Allow write access" (solo lectura)
5. Add key

> El archivo `modecissions-deploy-key` (privada) se carga en AWS Secrets
> Manager antes de crear la EC2 App. **Nunca commitearlo ni pasarlo como
> variable Terraform**, porque `user_data` queda en el state.

---

## 2. Terraform — Infraestructura — 20 min

```powershell
cd infra\terraform\infra
terraform init

terraform apply -target=aws_secretsmanager_secret.app

aws secretsmanager put-secret-value `
  --secret-id modecissions/github_deploy_key `
  --secret-string (Get-Content ..\modecissions-deploy-key -Raw)

terraform apply `
  -var="github_repo_url=git@github.com:ORG/REPO.git" `
  -var="deploy_ref=v1.0.0-rc3" `
  -var="image_tag=v1.0.0-rc3" `
  -var="public_console_domain=console.example.com" `
  -var="public_workspace_domain=workspace.example.com" `
  -var="alarm_email=ops@example.com"
```

> Bash equivalente para la clave privada:
> `aws secretsmanager put-secret-value --secret-id modecissions/github_deploy_key --secret-string "$(cat ../modecissions-deploy-key)"`.

**Recursos creados y tiempo aproximado**:

| Recurso                         | Tiempo  | Notas                                    |
|---------------------------------|---------|------------------------------------------|
| VPC + subnets + IGW + NAT GW    | ~3 min  | NAT GW es lo más lento del bloque        |
| Security groups (sg_app, sg_vpn) | <1 min | |
| Public ALB + target groups       | ~3 min | console/workspace only                  |
| ACM cert + DNS validation        | variable | automatic with Route53; manual otherwise |
| S3 bucket                       | <1 min  | private + versioning + SSE-S3            |
| IAM role + instance profile     | <1 min  | permite a EC2 App acceso a S3            |
| EC2 VPN (`t3.nano`)             | ~2 min  | user_data instala Docker + wg-easy       |
| EC2 App (`m6i.xlarge`)          | ~3 min  | user_data clona repo, instala Docker     |
| **Total**                       | ~10 min | sin RDS, mucho más rápido               |

**Guardar outputs** (los usaremos en pasos siguientes):

```powershell
terraform output -json > ..\terraform-outputs.json
Get-Content ..\terraform-outputs.json
```

Outputs esperados:

```json
{
  "ec2_vpn_public_ip":  { "value": "54.x.x.x" },
  "ec2_app_private_ip": { "value": "10.0.2.x" },
  "s3_bucket_name":     { "value": "modecissions-lakehouse-xxx" },
  "public_console_url": { "value": "https://console.example.com" },
  "public_workspace_url": { "value": "https://workspace.example.com" },
  "ssm_app_command":    { "value": "aws ssm start-session --target i-..." },
  "ssm_vpn_command":    { "value": "aws ssm start-session --target i-..." }
}
```

---

## 3. Setup VPN WireGuard — 10 min

En producción `51821/tcp` no debe quedar público. Usa port-forwarding por SSM
para abrir wg-easy localmente:

```powershell
terraform output ssm_wg_easy_port_forward_command
# Ejecuta el comando impreso y abre http://127.0.0.1:51821
```

Flujo:

1. Login en `http://127.0.0.1:51821` con la password definida en `infra/terraform/infra/user_data/vpn.sh.tpl`.
2. Crear un peer y descargar el `.conf`.
3. Importar el `.conf` en WireGuard.
4. Activar el tunnel y hacer `Test-Connection` a la EC2 App privada.

El script `infra/terraform/access/vpn-setup.ps1` queda como helper legacy para
casos donde `vpn_admin_allowed_cidrs` abre temporalmente `51821/tcp` a un CIDR
explícito. No usar `0.0.0.0/0`.

**Verificar tunnel activo** (en otra ventana PowerShell):

```powershell
Get-Service "WireGuardTunnel*" | Where-Object Status -eq Running
Test-Connection 10.0.2.15 -Count 2     # debe responder
```

> ⚠ El primer paso (`/installtunnelservice`) requiere PowerShell **como Administrador**.

---

## 4. Verificar EC2 App — 5 min

Usa SSM, no SSH público:

```powershell
terraform output ssm_app_command
# Ejecuta el comando impreso.
```

Una vez dentro de la EC2:

```bash
# 1. user_data terminó OK?
cat /var/log/userdata.log | tail -50

# 2. Repo clonado?
ls -la /opt/modecissions/
# Debes ver: console/  refinement/  rag/  mcp-infra/  airflow/  cartridges/  deploy/  ...

# 3. Marcador de READY (lo crea el user_data al final)
cat /opt/modecissions/READY
# bootstrap completed at 2026-04-28T18:42:12Z

# 4. Docker corriendo?
docker --version
docker compose version
sudo systemctl status docker --no-pager
```

Si `READY` no existe todavía, espera 2-3 min más y verifica `cloud-init-output.log` (paso 10).

---

## 5. Postgres en contenedor — sin paso manual

A diferencia de RDS, el Postgres es un contenedor Docker en la misma EC2 App. El bootstrap de las DBs (`modecissions`, `superset`, `airflow`, `modecissions_gold`) y la extensión `pgvector` se ejecuta automáticamente la primera vez que arranca el contenedor, vía los scripts de `infra/init/` y `infra/init_gold/` que están montados en `/docker-entrypoint-initdb.d`.

`start.sh` levanta `postgres` y `postgres_gold` antes que el resto, y espera 30s a que estén listos.

**Verificar (después del paso 8 — start.sh)**:

```bash
docker exec mode_postgres psql -U postgres -c '\l'
# Deben aparecer:  modecissions, superset, airflow

docker exec mode_postgres psql -U postgres -d modecissions -c '\dx'
# Debe listar la extensión 'vector'

docker exec mode_postgres_gold psql -U postgres -p 5433 -c '\l'
# Debe aparecer: modecissions_gold
```

---

## 6. Configurar AWS Secrets Manager — 5 min

La EC2 App ya no se configura editando secretos en un `.env` manual.
Terraform crea los secretos en AWS Secrets Manager y el boot de la EC2
ejecuta `scripts/aws-entrypoint.sh`, que escribe
`/opt/modecissions/infra/terraform/deploy/.env` con `umask 077`
(solo root puede leerlo). Si falta un secreto obligatorio, el script
falla y el stack no debe arrancar.

Terraform crea los contenedores de secretos vacíos en AWS Secrets
Manager. Este paso se ejecuta **después del apply parcial**
`terraform apply -target=aws_secretsmanager_secret.app` y **antes del
apply completo** que crea la EC2 App; si cargas secretos después de
crear la EC2, el `user_data` fallará por diseño. Los valores **no** se
pasan como variables Terraform para que no queden dentro del state.

```bash
aws secretsmanager put-secret-value --secret-id modecissions/postgres_password --secret-string '<password-seguro>'
aws secretsmanager put-secret-value --secret-id modecissions/jwt_secret_key --secret-string '<64+ chars>'
aws secretsmanager put-secret-value --secret-id modecissions/internal_api_key --secret-string '<64+ chars>'
aws secretsmanager put-secret-value --secret-id modecissions/vault_encryption_key --secret-string '<fernet-key>'
aws secretsmanager put-secret-value --secret-id modecissions/field_encryption_key --secret-string '<fernet-key>'
aws secretsmanager put-secret-value --secret-id modecissions/omega_console_password --secret-string '<role-password>'
aws secretsmanager put-secret-value --secret-id modecissions/omega_refinement_password --secret-string '<role-password>'
aws secretsmanager put-secret-value --secret-id modecissions/omega_vault_password --secret-string '<role-password>'
aws secretsmanager put-secret-value --secret-id modecissions/omega_workspace_password --secret-string '<role-password>'
aws secretsmanager put-secret-value --secret-id modecissions/omega_mcp_infra_password --secret-string '<role-password>'
aws secretsmanager put-secret-value --secret-id modecissions/omega_refinement_gold_password --secret-string '<role-password>'
aws secretsmanager put-secret-value --secret-id modecissions/omega_airflow_dag_password --secret-string '<role-password>'
aws secretsmanager put-secret-value --secret-id modecissions/omega_airflow_meta_password --secret-string '<role-password>'
aws secretsmanager put-secret-value --secret-id modecissions/omega_superset_meta_password --secret-string '<role-password>'
aws secretsmanager put-secret-value --secret-id modecissions/omega_cartridge_sap_hcm_password --secret-string '<role-password>'
aws secretsmanager put-secret-value --secret-id modecissions/omega_cartridge_sap_s4_password --secret-string '<role-password>'
aws secretsmanager put-secret-value --secret-id modecissions/omega_cartridge_sap_sf_password --secret-string '<role-password>'
aws secretsmanager put-secret-value --secret-id modecissions/omega_cartridge_replicon_password --secret-string '<role-password>'
aws secretsmanager put-secret-value --secret-id modecissions/airflow_secret_key --secret-string '<64+ chars>'
aws secretsmanager put-secret-value --secret-id modecissions/airflow_admin_password --secret-string '<password-seguro>'
aws secretsmanager put-secret-value --secret-id modecissions/agent_runner_token --secret-string '<64+ chars>'
aws secretsmanager put-secret-value --secret-id modecissions/superset_secret_key --secret-string '<64+ chars>'
aws secretsmanager put-secret-value --secret-id modecissions/superset_admin_password --secret-string '<password-seguro>'
aws secretsmanager put-secret-value --secret-id modecissions/superset_service_password --secret-string '<password-seguro>'
aws secretsmanager put-secret-value --secret-id modecissions/github_deploy_key --secret-string "$(cat ../modecissions-deploy-key)"
aws secretsmanager put-secret-value --secret-id modecissions/anthropic_api_key --secret-string '<requerido-si-CHAT_LLM_PROVIDER=anthropic>'
aws secretsmanager put-secret-value --secret-id modecissions/gemini_api_key --secret-string ''
aws secretsmanager put-secret-value --secret-id modecissions/smtp_password --secret-string ''
```

Además, carga todas las llaves direccionales `INTERNAL_API_KEY_*` declaradas
en `infra/terraform/infra/secretsmanager.tf`. El entrypoint falla cerrado si
cualquiera de esas llaves obligatorias falta.

| Variable                | Valor                                                    | De dónde sacarlo                       |
|-------------------------|----------------------------------------------------------|----------------------------------------|
| `POSTGRES_PASSWORD`     | `modecissions/postgres_password`                         | AWS Secrets Manager                    |
| `JWT_SECRET_KEY`        | `modecissions/jwt_secret_key`                            | AWS Secrets Manager                    |
| `INTERNAL_API_KEY`      | `modecissions/internal_api_key`                          | AWS Secrets Manager                    |
| `FIELD_ENCRYPTION_KEY`  | `modecissions/field_encryption_key`                      | AWS Secrets Manager                    |
| `S3_BUCKET_NAME`        | `modecissions-lakehouse-xxx`                             | `terraform output s3_bucket_name`      |
| `AWS_REGION`            | `us-east-1`                                              | tu región del apply                    |
| `ANTHROPIC_API_KEY`     | `modecissions/anthropic_api_key`                         | AWS Secrets Manager                    |
| `GEMINI_API_KEY`        | `modecissions/gemini_api_key`                            | AWS Secrets Manager, opcional puede ser vacío |
| `CHAT_LLM_PROVIDER`     | `anthropic`                                              | fijo                                   |
| `CHAT_LLM_MODEL`        | `claude-haiku-4-5-20251001`                              | fijo (ajustable)                       |
| `SQL_LLM_MODEL`         | `claude-sonnet-4-6`                                      | fijo                                   |
| `OLLAMA_URL`            | `http://host.docker.internal:11434`                      | legacy/local si usas Ollama            |
| `BEDROCK_REGION`        | mismo valor que `AWS_REGION`                             | región del runtime Bedrock             |
| `EMBED_MODEL`           | `amazon.titan-embed-text-v2:0`                           | default RAG en Bedrock                 |
| `EMBED_DIM`             | `1024`                                                   | debe coincidir con Titan v2            |
| `SUPERSET_SECRET_KEY`   | hex de 32 bytes                                          | `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `SUPERSET_ADMIN_USER`   | `admin`                                                  | usuario bootstrap de Superset        |
| `SUPERSET_ADMIN_PASSWORD` | password fuerte                                        | inventado / gestor                     |
| `AIRFLOW_SECRET_KEY`    | hex de 32 bytes                                          | mismo comando que Superset             |
| `CONSOLE_URL`           | `https://console.example.com`                            | Terraform `public_console_domain`      |
| `WORKSPACE_PUBLIC_URL`  | `https://workspace.example.com`                          | Terraform `public_workspace_domain`    |
| `SMTP_HOST`             | `mailhog` (default — captura emails sin enviarlos)       | mantener hasta tener SES configurado   |
| `SMTP_PORT`             | `1025`                                                   | fijo para MailHog                      |
| `SMTP_PASSWORD`         | `modecissions/smtp_password`                             | AWS Secrets Manager, opcional puede ser vacío |
| `SMTP_FROM`             | `noreply@modecissions.local`                             | fijo para MailHog                      |
| `INVITE_TOKEN_TTL_HOURS`| `72`                                                     | fijo (ajustable)                       |
| `RESET_TOKEN_TTL_HOURS` | `1`                                                      | fijo                                   |

Rotación de secretos:

```bash
aws secretsmanager put-secret-value \
  --secret-id modecissions/jwt_secret_key \
  --secret-string '<nuevo-valor>'
sudo bash /opt/modecissions/scripts/aws-entrypoint.sh
sudo docker compose --env-file /opt/modecissions/infra/terraform/deploy/.env \
  -f /opt/modecissions/infra/terraform/deploy/docker-compose.aws.yml up -d
```

---

## 7. Pull de imágenes Docker — 5-10 min

```bash
bash /opt/modecissions/infra/terraform/deploy/build.sh
```

Descarga las imágenes versionadas desde GHCR usando `GHCR_OWNER` e
`IMAGE_TAG` escritos por `aws-entrypoint.sh` en `.env`. En producción
`IMAGE_TAG` debe ser un tag inmutable de release; `latest` o vacío hacen
fallar el despliegue. El compose AWS ya no consume `modecissions/*:latest`;
si cambias el tag de release, actualiza `IMAGE_TAG` y vuelve a ejecutar este paso.

**Monitoreo en otra sesión SSM**:

```bash
aws ssm start-session --target <app-instance-id> --region us-east-1

# Progreso en vivo (qué se está buildeando ahora)
docker ps -a --format 'table {{.Names}}\t{{.Status}}'

# Espacio en disco (los builds consumen)
df -h /var/lib/docker

# CPU / RAM
top -bn1 | head -20

# Ver imágenes ya construidas
docker images | grep modecissions
```

**Resultado esperado** al terminar:

```
ghcr.io/OWNER/console      v1.0.0-rc3  ...  ~1.2 GB
ghcr.io/OWNER/workspace    v1.0.0-rc3  ...  ~900 MB
ghcr.io/OWNER/refinement   v1.0.0-rc3  ...  ~1.0 GB
ghcr.io/OWNER/mcp-infra    v1.0.0-rc3  ...  ~800 MB
```

> Si el build falla por OOM, ver Troubleshooting (sección 10).

---

## 8. Arrancar servicios — 15 min

```bash
bash /opt/modecissions/infra/terraform/deploy/start.sh
```

`start.sh` valida `.env`, levanta los init containers (`superset-init`, `airflow-init`), espera 60s a que terminen migraciones, y luego `up -d` del resto.

**Verificar**:

```bash
docker compose -f /opt/modecissions/infra/terraform/deploy/docker-compose.aws.yml ps
```

Estado esperado:

| Servicio              | Estado            |
|-----------------------|-------------------|
| mode_postgres         | Up                |
| mode_postgres_gold    | Up                |
| mode_console          | Up                |
| mode_workspace        | Up                |
| mode_refinement       | Up                |
| mode_mcp_infra        | Up                |
| mode_mailhog          | Up                |
| mode_superset         | Up                |
| mode_airflow          | Up                |
| mode_airflow_scheduler| Up                |
| mode_superset_init    | Exited (0)        |
| mode_airflow_init     | Exited (0)        |

> Los `_init` deben estar en `Exited (0)` — eso es éxito, no error.

Si algún servicio queda en `Restarting`, ver Troubleshooting.

---

## 9. Smoke tests — 5 min

Para v1 pública, el smoke obligatorio va contra los dominios HTTPS:

```bash
PUBLIC_CONSOLE_URL=https://console.example.com \
PUBLIC_WORKSPACE_URL=https://workspace.example.com \
TEST_EMAIL=admin@example.com \
TEST_PASSWORD=... \
E2E_LIVE_LLM=1 \
ANTHROPIC_API_KEY=... \
make verify-v1-public
```

Resultado esperado:

```
[OK] console /healthz
[OK] console /readyz
[OK] workspace /healthz
[OK] HTTP redirects to HTTPS
[OK] login cookies are HttpOnly, Secure and SameSite
[OK] replicon live test_connection
[OK] sap_hcm live test_connection
[OK] sap_s4hana live test_connection
[OK] sap_successfactors live test_connection
[OK] direct internal ports are not publicly reachable
[OK] Playwright public E2E
```

Los servicios internos se validan por VPN/SSM con `scripts/smoke_test.sh` desde
la EC2 App cuando haga falta diagnóstico interno.

Abrir desde el navegador:

- Console público:    https://console.example.com        ← login + Studio + admin
- Workspace público:  https://workspace.example.com      ← end-user (apps + asistente + decisiones)
- Superset:           http://10.0.2.X:8088               ← solo con VPN/SSM
- Airflow:            http://10.0.2.X:8082               ← solo con VPN/SSM
- MailHog UI:         http://10.0.2.X:8025               ← solo con VPN/SSM

Validación pública obligatoria:

```bash
PUBLIC_CONSOLE_URL=https://console.example.com \
PUBLIC_WORKSPACE_URL=https://workspace.example.com \
TEST_EMAIL=admin@example.com \
TEST_PASSWORD=... \
E2E_LIVE_LLM=1 \
ANTHROPIC_API_KEY=... \
make verify-v1-public
```

---

### Superset datasets desde Studio

El botón **Crear en Superset** de Studio usa el backend `console` contra la API
REST de Superset (`/api/v1/security/login`, CSRF y `/api/v1/dataset/`). Para
que funcione en AWS, `docker-compose.aws.yml` fija `SUPERSET_URL=http://superset:8088`
y `scripts/aws-entrypoint.sh` inyecta `SUPERSET_ADMIN_USER` /
`SUPERSET_ADMIN_PASSWORD`. Para reducir blast radius, configura también
`SUPERSET_SERVICE_USER` y el secreto `SUPERSET_SERVICE_PASSWORD` con una cuenta
de servicio limitada a listar bases y crear datasets.

Primer setup recomendado:

1. Levantar Superset y entrar con `SUPERSET_ADMIN_USER` /
   `SUPERSET_ADMIN_PASSWORD`.
2. Crear opcionalmente un usuario de servicio para Studio y guardarlo como
   `SUPERSET_SERVICE_USER` / `SUPERSET_SERVICE_PASSWORD`.
3. Registrar una conexión de base de datos hacia Postgres Gold
   (`modecissions_gold`) si no existe.
4. Desde Studio, ejecutar **Crear en Superset** sobre una tabla Gold. Si el
   dataset ya existe, el backend devuelve el dataset existente en vez de crear
   duplicados.

---

## 9.5 Bootstrap del primer admin — 1 min

La auth local empieza vacía. Crea el primer admin manualmente vía CLI:

```bash
docker compose -f /opt/modecissions/infra/terraform/deploy/docker-compose.aws.yml exec console \
  BOOTSTRAP_ADMIN_PASSWORD='<password-temporal-fuerte>' \
  BOOTSTRAP_ADMIN_FULL_NAME='<Tu Nombre>' \
  python -m app.bootstrap_admin <tu-email@org.com>
```

Resultado esperado:

```
Created admin user id=1 email=tu-email@org.com
```

Ahora puedes hacer login en `http://10.0.2.X:8000/login` con ese email y password.
Después invita a tu equipo desde `/admin/users` (los emails caen en MailHog hasta
que enchufemos SES).

> Nota: el password queda forzado a cambio en el primer login (`must_change_password=true`)
> — es el comportamiento normal del flow de bootstrap.

---

## 10. Troubleshooting común

### Postgres connection refused

```bash
# Desde EC2 App
docker ps --filter name=mode_postgres --format '{{.Status}}'
docker exec mode_postgres pg_isready -U postgres
```

- Si el contenedor no levanta: `docker logs mode_postgres --tail 50`. Causa común: volumen `postgres_data` con permisos rotos — `docker volume rm postgres_data` (¡borra datos!) y reinicia.
- Si los servicios no resuelven `postgres`: confirma que están en la misma red `modecissions_net` (`docker network inspect modecissions_net`).

### user_data no terminó

```bash
sudo cat /var/log/cloud-init-output.log | tail -100
sudo tail -f /var/log/cloud-init-output.log
```

- Si está corriendo `apt-get` o `git clone`, espera 5 min más.
- Si terminó pero `READY` no existe, hay un error en algún paso del user_data. Revisa el log.

### Build falla (OOM — Killed)

```bash
docker system prune -f
free -h     # confirma RAM disponible
```

Bajar una imagen específica si hace falta:

```bash
cd /opt/modecissions/infra/terraform/deploy
docker compose -f docker-compose.aws.yml pull console
docker compose -f docker-compose.aws.yml pull refinement
docker compose -f docker-compose.aws.yml pull vault
docker compose -f docker-compose.aws.yml pull mcp-infra
```

Si persiste, agrega swap temporal (la `m6i.xlarge` tiene 16 GB RAM pero los wheels grandes pueden picarla):

```bash
sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
```

### Container crashea en loop

```bash
cd /opt/modecissions/infra/terraform/deploy
docker compose -f docker-compose.aws.yml logs <servicio> --tail=50
```

Causas frecuentes:

- Variable faltante en `.env` (mira el primer `KeyError` o `ValidationError` del log).
- Contenedor `postgres` no listo todavía: espera 30s y reintenta `docker compose up -d`.
- `.env` con permisos demasiado abiertos para PG: `chmod 600 .env`.

### Superset-init falla

Es **idempotente** — re-ejecutar es seguro:

```bash
cd /opt/modecissions/infra/terraform/deploy
docker compose -f docker-compose.aws.yml up superset-init
```

Si falla otra vez, revisa que la DB `superset` exista: `docker exec mode_postgres psql -U postgres -c '\l' | grep superset`.

### WireGuard no conecta

1. **Security group** `sg_vpn` debe abrir UDP `51820` desde `0.0.0.0/0`:
   ```bash
   aws ec2 describe-security-groups --group-ids sg-xxx --query 'SecurityGroups[].IpPermissions'
   ```
2. Entra a la EC2 VPN por SSM:
   ```bash
   aws ssm start-session --target <vpn-instance-id> --region us-east-1
   docker ps | grep wg-easy
   docker logs wg-easy --tail=50
   ```
3. En Windows, reiniciar el tunnel:
   ```powershell
   Stop-Service "WireGuardTunnel`$laptop-rodolfo"
   Start-Service "WireGuardTunnel`$laptop-rodolfo"
   ```

---

## 11. Operación diaria

**Ver logs**:

```bash
bash /opt/modecissions/infra/terraform/deploy/logs.sh             # todos
bash /opt/modecissions/infra/terraform/deploy/logs.sh console     # solo console
```

**Actualizar un servicio** (nuevo tag ya publicado en GHCR):

```bash
bash /opt/modecissions/infra/terraform/deploy/update.sh console
```

**Actualizar todo** (checkout de `DEPLOY_REF` + pull de imágenes + restart):

```bash
# En producción DEPLOY_REF e IMAGE_TAG son obligatorios y deben apuntar a
# la misma release inmutable.
bash /opt/modecissions/infra/terraform/deploy/update.sh
```

**Acceso operativo por SSM**:

```bash
terraform output ssm_app_command
terraform output ssm_vpn_command
```

SSH público queda deshabilitado por defecto. Solo se habilita si
`ssh_allowed_cidrs` contiene CIDRs explícitos; nunca usar `0.0.0.0/0`.

**Backup operativo** (desde EC2 App):

```bash
bash /opt/modecissions/infra/terraform/deploy/backup.sh
```

**Restore controlado**:

```bash
BACKUP_ID=20260530T220000Z-v1.0.0-rc3 \
CONFIRM_RESTORE=modecissions \
bash /opt/modecissions/infra/terraform/deploy/restore.sh
```

**Rollback de release**:

```bash
bash /opt/modecissions/infra/terraform/deploy/rollback.sh v1.0.0-rc2
```

> Backups, restores y rollbacks deben terminar con smoke verde antes de declarar v1 saludable.
> `RESTORE_DELETE_STALE_S3=1` requiere `RESTORE_DELETE_PREFIX` y rechaza
> prefijos peligrosos como `/` o `backups`.

**Reiniciar la EC2 App** (mantenimiento):

```bash
# Antes: detener compose limpiamente
cd /opt/modecissions/infra/terraform/deploy
docker compose -f docker-compose.aws.yml down

# Reiniciar
sudo reboot

# Al volver: docker compose up -d  (o start.sh)
```

---

## 12. Costos mensuales estimados

Tarifas us-east-1, on-demand, 730 h/mes.

| Recurso                 | Spec                 | Costo/mes    |
|-------------------------|----------------------|--------------|
| EC2 App                 | m6i.xlarge (4 vCPU, 16 GB) | ~$140 |
| EC2 VPN                 | t3.nano              | ~$4          |
| S3                      | 50 GB + requests     | ~$2          |
| **NAT Gateway**         | 730 h + 50 GB egress | **~$35**     |
| Data transfer out       | ~10 GB               | ~$1          |
| EBS (root volumes)      | 150 GB + 8 GB gp3    | ~$15         |
| **Total**               |                      | **~$197/mes**|

> Postgres ahora vive en contenedor en el EBS del EC2 App: ~$60/mes ahorrados vs RDS db.t3.medium.

> **Optimización: el NAT Gateway es el ítem más caro relativamente**. Sirve sólo para que la EC2 App (en subnet privada) llegue a internet (S3, ECR, GitHub, pip, npm).
>
> Reemplazándolo por **VPC Endpoints** ahorras ~$30/mes:
>
> - **Gateway endpoint para S3** (gratis) → resuelve el 80% del tráfico (lakehouse).
> - **Interface endpoint para ECR/Logs** (~$7/mes c/u) si usas ECR.
> - Para `git clone` y `pip install`: o bien dejas un NAT más pequeño temporal, o haces los builds en una EC2 con IP pública (build server) y publicas las imágenes a un registry interno.
>
> Para este setup (build local en EC2 App), un compromiso razonable es:
> NAT Gateway sólo durante deploys y `update.sh`, apagado el resto del tiempo (`aws ec2 delete-nat-gateway`/recrear). No automatizado en este runbook — ver `infra/vpc.tf` para implementar.

---

## 13. Diagrama de arquitectura

```
                        Internet
                           │
                           ├── HTTPS 443 / HTTP 80 redirect
                           ▼
                    Public ALB (console/workspace)
                           │
                           └── private targets :8000/:8001
                               to EC2 App sg_app

                        Operator
                           │
           ┌───────────────┴────────────────┐
           │                                │
           │     AWS VPC  (10.0.0.0/16)     │
           │     us-east-1                  │
           │                                │
           │   ┌──────────────────────┐     │
           │   │ Public subnet        │     │
           │   │ 10.0.1.0/24          │     │
           │   │                      │     │
   ┌───────┼───┤  ┌────────────────┐  │     │
   │  UDP  │   │  │ EC2 VPN        │  │     │
   │ 51820 ├───┼──┤ t3.nano        │  │     │
   │       │   │  │ wg-easy:51821  │  │     │
   │ SSM/  │   │  │ public IP      │  │     │
   │ CIDR  │   │  │ sg_vpn         │  │     │
   │ only  │   │  └────────────────┘  │     │
   │       │   │                      │     │
   │       │   │  ┌────────────────┐  │     │
   │       │   │  │   NAT Gateway  │  │     │
   │       │   │  └────────┬───────┘  │     │
   │       │   └───────────┼──────────┘     │
   │       │               │                │
   │       │   ┌───────────┴──────────┐     │
   │       │   │ Private subnet       │     │
   │       │   │ 10.0.2.0/24          │     │
   │       │   │                      │     │
   │ (VPN) │   │  ┌────────────────┐  │     │
   │       │   │  │ EC2 App        │  │     │
   └───────┼───┼─►│ m6i.xlarge     │  │     │
  WireGuard│   │  │ 10.0.2.X       │  │     │
   peers   │   │  │ sg_app         │  │     │
   10.8.0.0│   │  │                │  │     │
   /24     │   │  │ Docker:        │  │     │
           │   │  │  console  :8000│  │     │
           │   │  │  workspace:8001│  │     │
           │   │  │  mcp-infra:8010│  │     │
           │   │  │  mailhog  :8025│  │     │
           │   │  │  superset:8088 │  │     │
           │   │  │  airflow :8082 │  │     │
           │   │  │  refine  :8500 │  │     │
           │   │  └────┬───────────┘  │     │
           │   │       │              │     │
           │   │       │              │     │
           │   │       │ Postgres en  │     │
           │   │       │ contenedor   │     │
           │   │       │ (mismo EC2): │     │
           │   │       │  postgres    │     │
           │   │       │  pgvector pg15│    │
           │   │       │  postgres_gold│    │
           │   │       │  vol persistente   │
           │   │       │  modecissions │    │
           │   │       │  superset    │     │
           │   │       │  airflow     │     │
           │   │       │  _gold       │     │
           │   └──────────────────────┘     │
           │                                │
           │   ┌──────────────────────┐     │
           │   │ S3 (regional)        │     │
           │   │ modecissions-        │     │
           │   │ lakehouse-xxx        │     │
           │   │ (privado, SSE-S3)    │     │
           │   │ accedido via IAM     │     │
           │   │ role de EC2 App      │     │
           │   └──────────────────────┘     │
           └────────────────────────────────┘

Reglas de Security Groups
─────────────────────────
sg_alb (ALB público)
  Ingress:  TCP 80   ← 0.0.0.0/0    (redirect a 443)
            TCP 443  ← 0.0.0.0/0    (HTTPS console/workspace)
  Egress:   TCP 8000 → sg_app
            TCP 8001 → sg_app

sg_vpn  (EC2 VPN)
  Ingress:  UDP 51820  ← 0.0.0.0/0    (WireGuard)
            TCP 51821  ← CIDRs explícitos solamente o SSM port-forward
            TCP 22     ← cerrado por defecto; solo `ssh_allowed_cidrs` explícitos
  Egress:   ALL → 0.0.0.0/0

sg_app  (EC2 App)
  Ingress:  ALL traffic from sg_vpn (security_groups ref, no fija puertos)
            → cubre 8000, 8001, 8010, 8025, 8082, 8088, 8500
            TCP 8000 ← sg_alb
            TCP 8001 ← sg_alb
  Egress:   ALL → 0.0.0.0/0   (vía NAT GW)

Flujo de un request público
────────────────────────────────
Browser ──HTTPS──► ALB ──HTTP target privado──► EC2 App :8000/:8001 ──► postgres (docker) :5432
                                                 │
                                                 └──► S3 (vía NAT GW + IAM role)
```

---

## Apéndice — Checklist resumido

- [ ] §0  Herramientas instaladas y `aws sts get-caller-identity` OK
- [ ] §1  Deploy key generada y registrada en GitHub
- [ ] §2  `terraform apply` exitoso, outputs guardados en `terraform-outputs.json`
- [ ] §3  Tunnel WireGuard activo, `Test-Connection` a EC2 App OK
- [ ] §4  `cat /opt/modecissions/READY` muestra timestamp
- [ ] §5  `docker exec mode_postgres psql ... \l` lista las 3 DBs, extensión `vector` presente
- [ ] §6  `.env` completo, `chmod 600`
- [ ] §7  4 imágenes `modecissions/*` en `docker images`
- [ ] §8  `docker compose ps` muestra todos los servicios `Up` (init en `Exited 0`)
- [ ] §9  `make verify-v1-public` verde contra dominios HTTPS
- [ ] §11 `backup.sh`, `restore.sh` y `rollback.sh` ejecutados en staging con smoke verde

---

## v1.43.1 — Cartridges deployed separately (Codex P0-4)

This compose file (**`docker-compose.aws.yml`**) **does NOT include
the 4 cartridges** (`replicon`, `sap_hcm`, `sap_s4hana`,
`sap_successfactors`). Reason: cartridges have independent scaling +
release cadence from the core platform and typically live in a
separate compute pool (their own EC2, ECS service, or Kubernetes
namespace).

What the AWS compose **does** ship:

1. **DAG mounts** — `cartridges/<c>/dags/` is mounted into the
   Airflow workers, so the DAGs still parse and schedule.
2. **Cartridge URL env vars** — `SAP_HCM_URL`, `SAP_S4HANA_URL`,
   `SAP_SUCCESSFACTORS_URL`, `REPLICON_URL` are threaded into both
   `airflow` and `airflow-scheduler`. The DAGs read these env vars
   (v1.43.1 Claude B2 hardening) so the operator points them at
   wherever the cartridges actually run.

### Deploy options

| Pattern | When to use |
|---|---|
| **A. Same host (escape hatch)** | Staging / dev clusters where compute pressure is low. Run `docker compose -f docker-compose.aws.yml -f docker-compose.cartridges.yml up -d` with a sibling compose file that adds the 4 services. Defaults of `http://sap-hcm:8202` etc. already match. |
| **B. Separate cluster (production)** | Production. Cartridges run on their own EC2 / ECS / K8s with their own scaling rules. Set `SAP_HCM_URL=https://cart-sap-hcm.internal.example.com` etc. in the parent `.env`. Make sure security-group / NACL rules allow `airflow → cartridges:820X`. |

### Verification after deploy

```bash
# 1. DAGs parse — fail-fast on missing INTERNAL_API_KEY surfaces here.
docker exec mode_airflow_scheduler airflow dags list-import-errors

# 2. URLs resolve.
docker exec mode_airflow_scheduler sh -lc 'curl -sS -o /dev/null -w "%{http_code}\n" "$SAP_HCM_URL/health"'

# 3. Trigger a smoke run.
docker exec mode_airflow airflow dags trigger sap_hcm_extract \
  --conf '{"entity":"pa0001"}'
```

If §B is chosen, **DO NOT** add the cartridge service blocks back
into this file — they belong in their own deployment artifact.
