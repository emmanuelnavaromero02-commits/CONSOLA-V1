# MODecissions — Deploy Runbook (AWS)

Guía operacional end-to-end para desplegar MODecissions desde una laptop Windows nueva.

**Arquitectura objetivo**

| Componente   | Tipo / Spec                | Función                                  |
|--------------|----------------------------|------------------------------------------|
| EC2 App      | `m6i.xlarge`               | Docker Compose, build local, repo clonado |
| EC2 VPN      | `t3.nano`                  | WireGuard (wg-easy) en Docker             |
| Postgres     | Contenedores en EC2 App (`pgvector/pgvector:pg15` + `postgres:15`) | 4 databases (modecissions, _gold, superset, airflow) — paridad con local |
| S3           | Bucket privado             | Lakehouse (reemplaza MinIO)               |

**Servicios docker en EC2 App** (todos accesibles solo vía VPN):

| Servicio              | Puerto | Rol                                                         |
|-----------------------|--------|-------------------------------------------------------------|
| console               | 8000   | Builder/admin: Studio, Monitor, Decisiones, /admin/users    |
| workspace             | 8001   | End-user: apps publicadas + asistente IA + decisiones       |
| refinement            | 8500   | DuckDB + LLM SQL para datasets                              |
| mcp-infra             | 8010   | MCP tools para Airflow, Postgres, Superset, RAG, cartridges |
| superset              | 8088   | BI tradicional                                              |
| airflow               | 8082   | Orquestación de DAGs                                        |
| mailhog (UI)          | 8025   | Dev SMTP catcher (placeholder hasta SES)                    |
| mailhog (SMTP)        | 1025   | SMTP interno (consumido por console)                        |
| postgres              | 5432*  | DBs modecissions, superset, airflow                         |
| postgres_gold         | 5433*  | DB modecissions_gold (master + gold layer)                  |

\* Postgres ports no están expuestos al host — solo en la red `modecissions_net` interna.

**Acceso**: 100% vía VPN WireGuard. Ningún servicio expuesto a internet, salvo el panel wg-easy y SSH al bastión VPN.

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
| OpenSSH (ssh, ssh-keygen) | included | viene con Windows 10/11 — `Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0` |

**Comandos de verificación** (correr todos en una sola PowerShell):

```powershell
aws --version              # aws-cli/2.15.x
terraform version          # Terraform v1.6+
git --version              # git version 2.40+
$PSVersionTable.PSVersion  # 7.4.x
ssh -V                     # OpenSSH_for_Windows_8.1+
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

> El archivo `modecissions-deploy-key` (privada) lo pasaremos a Terraform como variable. **Nunca commitearlo**.

---

## 2. Terraform — Infraestructura — 20 min

```powershell
cd infra/
terraform init

terraform apply `
  -var="postgres_password=CAMBIAR_POR_PASSWORD_SEGURO" `
  -var="github_repo_url=git@github.com:ORG/REPO.git" `
  -var="deploy_private_key=$(Get-Content ..\modecissions-deploy-key -Raw)"
```

> Bash equivalente: `-var="deploy_private_key=$(cat ../modecissions-deploy-key)"`.

**Recursos creados y tiempo aproximado**:

| Recurso                         | Tiempo  | Notas                                    |
|---------------------------------|---------|------------------------------------------|
| VPC + subnets + IGW + NAT GW    | ~3 min  | NAT GW es lo más lento del bloque        |
| Security groups (sg_app, sg_vpn) | <1 min | |
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
  "s3_bucket_name":     { "value": "modecissions-lakehouse-xxx" }
}
```

---

## 3. Setup VPN WireGuard — 10 min

Desde Windows, con los outputs de Terraform:

```powershell
cd ..\access\

# Reemplazar con los valores reales de terraform-outputs.json
.\vpn-setup.ps1 `
  -VpnIp 54.123.45.67 `
  -AppIp 10.0.2.15 `
  -PemPath ..\modecissions-key.pem
```

El script:

1. Abre `http://<VPN_IP>:51821` en el navegador (password: `M4n4g3rWG27*`, definido como bcrypt hash en `infra/terraform/infra/user_data/vpn.sh.tpl`).
2. Te guía a crear un peer y descargar el `.conf`.
3. Detecta el `.conf` más reciente en `~/Downloads` y lo importa como servicio Windows.
4. Activa el tunnel y hace `Test-Connection` a la EC2 App.

**Verificar tunnel activo** (en otra ventana PowerShell):

```powershell
Get-Service "WireGuardTunnel*" | Where-Object Status -eq Running
Test-Connection 10.0.2.15 -Count 2     # debe responder
```

> ⚠ El primer paso (`/installtunnelservice`) requiere PowerShell **como Administrador**.

---

## 4. Verificar EC2 App — 5 min

Con la VPN activa:

```powershell
cd ..\access\
.\ssh-app.ps1 -AppIp 10.0.2.15 -PemPath ..\modecissions-key.pem
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

## 6. Configurar .env — 5 min

```bash
cd /opt/modecissions/infra/terraform/deploy
cp .env.example .env
nano .env
```

| Variable                | Valor                                                    | De dónde sacarlo                       |
|-------------------------|----------------------------------------------------------|----------------------------------------|
| `POSTGRES_PASSWORD`     | el password que pasaste a `-var="postgres_password=..."` | tu gestor de secretos                  |
| `S3_BUCKET_NAME`        | `modecissions-lakehouse-xxx`                             | `terraform output s3_bucket_name`      |
| `AWS_REGION`            | `us-east-1`                                              | tu región del apply                    |
| `ANTHROPIC_API_KEY`     | `sk-ant-...`                                             | console.anthropic.com → API Keys       |
| `GEMINI_API_KEY`        | (opcional, si quieres Gemini)                            | aistudio.google.com                    |
| `CHAT_LLM_PROVIDER`     | `anthropic`                                              | fijo                                   |
| `CHAT_LLM_MODEL`        | `claude-haiku-4-5-20251001`                              | fijo (ajustable)                       |
| `SQL_LLM_MODEL`         | `claude-sonnet-4-6`                                      | fijo                                   |
| `OLLAMA_URL`            | `http://localhost:11434`                                 | si usas Ollama local                   |
| `EMBED_MODEL`           | `nomic-embed-text`                                       | fijo si usas Ollama                    |
| `EMBED_DIM`             | `768`                                                    | debe coincidir con `EMBED_MODEL`       |
| `SUPERSET_SECRET_KEY`   | hex de 32 bytes                                          | `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `SUPERSET_ADMIN_PASSWORD` | password fuerte                                        | inventado / gestor                     |
| `AIRFLOW_SECRET_KEY`    | hex de 32 bytes                                          | mismo comando que Superset             |
| `CONSOLE_URL`           | `http://10.0.2.X:8000` (la IP privada de EC2 App)        | `terraform output ec2_app_private_ip` |
| `WORKSPACE_PUBLIC_URL`  | `http://10.0.2.X:8001`                                   | misma IP, puerto 8001                  |
| `SMTP_HOST`             | `mailhog` (default — captura emails sin enviarlos)       | mantener hasta tener SES configurado   |
| `SMTP_PORT`             | `1025`                                                   | fijo para MailHog                      |
| `SMTP_FROM`             | `noreply@modecissions.local`                             | fijo para MailHog                      |
| `INVITE_TOKEN_TTL_HOURS`| `72`                                                     | fijo (ajustable)                       |
| `RESET_TOKEN_TTL_HOURS` | `1`                                                      | fijo                                   |

> Permisos: `chmod 600 /opt/modecissions/infra/terraform/deploy/.env` después de editarlo.

---

## 7. Build de imágenes Docker — 30-45 min

```bash
bash /opt/modecissions/infra/terraform/deploy/build.sh
```

Buildea las 4 imágenes locales: `console`, `workspace`, `refinement`, `mcp-infra` (RAG ya migró dentro de mcp-infra). La **primera vez** tarda 30-45 min porque baja todas las base images y resuelve `pip` de cero. Las siguientes corridas usan cache de capas y suelen tomar 2-5 min.

**Monitoreo en otra sesión SSH**:

```bash
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
modecissions/console      latest  ...  ~1.2 GB
modecissions/workspace    latest  ...  ~900 MB
modecissions/refinement   latest  ...  ~1.0 GB
modecissions/mcp-infra    latest  ...  ~800 MB
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

Desde Windows, con VPN activa:

```powershell
cd access\
.\smoke-test.ps1 -AppIp 10.0.2.15 -PemPath ..\modecissions-key.pem
```

Resultado esperado:

```
Servicio    Puerto  URL                              HTTP  Status
--------    ------  ---                              ----  ------
console     8000    http://localhost:8000/login      200   [OK]
workspace   8001    http://localhost:8001/healthz    200   [OK]
superset    8088    http://localhost:8088/health     200   [OK]
airflow     8082    http://localhost:8082/health     200   [OK]
refinement  8500    http://localhost:8500/health     200   [OK]
mcp-infra   8010    http://localhost:8010/health     200   [OK]
mailhog UI  8025    http://localhost:8025/           200   [OK]

7/7 servicios operativos
```

Abrir desde el navegador (con VPN activa):

- Console:    http://10.0.2.X:8000        ← login + Studio + admin
- Workspace:  http://10.0.2.X:8001        ← end-user (apps + asistente + decisiones)
- Superset:   http://10.0.2.X:8088        (admin / `$SUPERSET_ADMIN_PASSWORD`)
- Airflow:    http://10.0.2.X:8082        (admin / admin — cambiar después)
- MailHog UI: http://10.0.2.X:8025        ← lee aquí los emails de invitación/reset

---

## 9.5 Bootstrap del primer admin — 1 min

La auth local empieza vacía. Crea el primer admin manualmente vía CLI:

```bash
docker compose -f /opt/modecissions/infra/terraform/deploy/docker-compose.aws.yml exec console \
  python -m app.bootstrap_admin <tu-email@org.com> '<password-temporal-fuerte>' '<Tu Nombre>'
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

Buildear de a uno y con `--no-cache` para forzar limpieza si hace falta:

```bash
cd /opt/modecissions
docker build -t modecissions/console:latest ./console
docker build -t modecissions/refinement:latest ./refinement
docker build -t modecissions/rag:latest ./rag
docker build -t modecissions/mcp-infra:latest ./mcp-infra
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
2. SSH a la EC2 VPN (`ssh -i modecissions-key.pem ubuntu@<VPN_IP>`):
   ```bash
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

**Actualizar un servicio** (después de un cambio en su Dockerfile):

```bash
bash /opt/modecissions/infra/terraform/deploy/update.sh console
```

**Actualizar todo** (git pull + rebuild + restart):

```bash
bash /opt/modecissions/infra/terraform/deploy/update.sh
```

**Backup Postgres manual** (desde EC2 App):

```bash
# Dump de las 3 DBs principales
TS=$(date +%Y%m%d-%H%M)
docker exec mode_postgres pg_dumpall -U postgres > /tmp/pg-$TS.sql
docker exec mode_postgres_gold pg_dumpall -U postgres -p 5433 > /tmp/pg_gold-$TS.sql

# Subir a S3 (el IAM role del EC2 App ya tiene acceso)
aws s3 cp /tmp/pg-$TS.sql s3://$S3_BUCKET_NAME/backups/
aws s3 cp /tmp/pg_gold-$TS.sql s3://$S3_BUCKET_NAME/backups/
```

> **Nota**: ya no hay snapshots automáticos como con RDS. Se recomienda agendar este backup en cron (`crontab -e`).

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
   │  TCP  │   │  │ public IP      │  │     │
   │ 51821 │   │  │ sg_vpn         │  │     │
   │ (UI)  │   │  └────────────────┘  │     │
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
sg_vpn  (EC2 VPN)
  Ingress:  UDP 51820  ← 0.0.0.0/0    (WireGuard)
            TCP 51821  ← 0.0.0.0/0    (wg-easy UI; mover a VPN-only en prod)
            TCP 22     ← admin IP     (SSH)
  Egress:   ALL → 0.0.0.0/0

sg_app  (EC2 App)
  Ingress:  ALL traffic from sg_vpn (security_groups ref, no fija puertos)
            → cubre 8000, 8001, 8010, 8025, 8082, 8088, 8500 + SSH
  Egress:   ALL → 0.0.0.0/0   (vía NAT GW)

Flujo de un request del usuario
────────────────────────────────
Laptop ──WG tunnel──► EC2 VPN ──VPC route──► EC2 App :8000 ──► postgres (docker) :5432
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
- [ ] §9  `smoke-test.ps1` reporta 6/6
- [ ] §11 Backup `pg_dumpall` programado en cron — recurrente
