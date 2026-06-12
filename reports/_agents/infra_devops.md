# Infra / Docker / DevOps Audit — OMEGA/CONSOLA-BETA

Auditor: Infra/DevOps agent · HEAD `bab2a4f` · VERSION `1.45.68-beta` · Fecha: 2026-06-12
Método: evidencia file:line, solo lectura. Sin `docker run`, sin `terraform plan/apply`, sin triggers de workflows.

---

## 1. Matriz de servicios — infra/docker-compose.yml (1240 líneas)

**22 servicios confirmados** (19 runtime + 3 init one-shot: `postgres_dev_seed`, `superset-init`, `airflow-init`). Sin sección `networks:` — **todos en la red bridge default, sin segmentación** (el archivo termina en `volumes:` línea 1237-1240).

| Servicio | Imagen/Build | Healthcheck (real) | depends_on | restart | Puertos host | Notas |
|---|---|---|---|---|---|---|
| postgres | pgvector/pgvector:pg15 (l.6) | pg_isready TCP (l.31) | — | **AUSENTE** | 15432→5432 (l.24) | PGOPTIONS con 14 passwords de rol (l.22) |
| postgres_dev_seed | postgres:15 (l.37) | — | postgres healthy (l.40) | "no" (l.54) | — | Gated: salta si APP_ENV=prod (l.48-49); **nadie depende de él → fallo silencioso** |
| postgres_gold | postgres:15 (l.57) | pg_isready -p 5433 (l.71) | — | **AUSENTE** | 15433→5433 (l.65) | init_gold montado (l.69) |
| redis | redis:7-alpine (l.77) | redis-cli ping (l.84) | — | **AUSENTE** | (ninguno — bien) | maxmemory 64mb LRU (l.82) |
| minio | minio RELEASE.2024-12-18 pinned (l.94) | /minio/health/live (l.112) | — | **AUSENTE** | 9000, 9001 (l.101-102) | bind mount ../data/lakehouse (l.104-106) |
| console | build ../console (l.121-123) | python urllib /healthz (l.248) | postgres, vault, mcp-infra, **mailhog**, redis — todos healthy (l.233-243) | unless-stopped (l.253) | 8000 | APP_ENV `:-production` fail-closed (l.138); VERSION montado ro (l.232) |
| workspace | build ../workspace (l.256) | /healthz (l.302) | postgres, vault, refinement, mcp-infra, redis (l.287-297) | unless-stopped (l.307) | 8001 | |
| refinement | build ../refinement (l.310) | /healthz (l.360) | postgres, vault, postgres_gold, minio (l.347-355) | **AUSENTE** (l.309-364 sin restart) | 8500 | **Único con límites de recursos**: mem_limit/cpus (l.314-315) |
| vault | build ../vault (l.368) | /healthz (l.413) | postgres (l.406-408) | unless-stopped (l.418) | 8300 | VAULT_ENCRYPTION_KEY `:-` para CI config (l.398) |
| mailhog | mailhog/mailhog:v1.0.1 (l.421) | wget /api/v1/messages (l.430) | — | unless-stopped (l.426) | **1025, 8025** (l.424-425) | **En compose base prod-default, no dev-only** |
| replicon | build cartridge (l.444-446) | /health (no /healthz) (l.481) | postgres, minio (l.486-490) | unless-stopped (l.448) | 8201 | 3 pair-keys `:?` required (l.453-455) |
| salesforce | build (l.493-495) | /health (l.533) | postgres, minio | unless-stopped (l.497) | 8205 | |
| hubspot | build (l.547-549) | /health (l.582) | postgres, minio | unless-stopped (l.551) | 8210 | extra_hosts host-gateway para fake API (l.577-580) |
| sap-successfactors | **profile sap** (l.599), build | curl /health (l.653) | postgres, minio | unless-stopped (l.649) | 8203 | monta PEM opcional vía /dev/null fallback (l.641) |
| sap-hcm | **profile sap** (l.660) | curl /health (l.700) | postgres, minio | unless-stopped (l.696) | 8202 | |
| sap-s4hana | **profile sap** (l.707) | curl /health (l.748) | postgres, minio | unless-stopped (l.744) | 8204 | |
| mcp-infra | build ../mcp-infra (l.757) | /healthz (l.827) | postgres, postgres_gold, minio, vault, **airflow** healthy (l.812-822) | unless-stopped (l.832) | 8010 | **monta ../airflow/dags RW** ("write DAGs directly", l.811) |
| superset-init | apache/superset:**3.1.3** (l.845) | — | postgres, redis (l.917-921) | "no" (l.922) | — | `set -e`: db upgrade + upsert admin + re-encrypt + init + GRANTs a omega_superset_meta (l.868-916) |
| superset | apache/superset:**3.1.3** (l.925) | curl /health (l.964) | postgres, redis, superset-init **completed_successfully** (l.952-958) | unless-stopped (l.959) | 8088 | metastore con rol `omega_superset_meta` (l.935) |
| airflow-init | build ./airflow → mode-airflow:**2.10.5**-local (l.975-978) | — | postgres, vault healthy (l.1018-1022) | "no" (l.1027) | — | create DB + `airflow db migrate` + GRANTs a omega_airflow_meta + users create (l.983-986); `users create ... \|\| true` traga fallos (l.986) |
| airflow (webserver) | mode-airflow:2.10.5-local (l.1033) | curl /health (l.1136) | postgres, vault, airflow-init completed (l.1121-1131) | unless-stopped (l.1132) | **8082→8080** (l.1100) | LocalExecutor (l.1046); metastore postgres db `airflow` rol omega_airflow_meta (l.1052); basic_auth API (l.1062) |
| airflow-scheduler | mode-airflow:2.10.5-local (l.1146) | **proc-scan de cmdline** (real, no sleep) (l.1231) | igual a airflow (l.1215-1225) | unless-stopped (l.1226) | — | LocalExecutor (l.1163) |

Healthchecks: **todos reales** (HTTP/pg_isready/redis ping/proc-scan); ninguno es `sleep`/`true`. Calidad alta.

**DAGs**: cartridge DAGs montados `:ro` (l.1112-1120, 1206-1214); el directorio padre `../airflow/dags` montado **RW** con justificación documentada (mountpoints anidados EROFS, l.1102-1108). mcp-infra lo monta RW deliberadamente (l.811).

**Overlays**:
- `docker-compose.dev.yml` (18 líneas): solo `RATE_LIMIT_ENABLED=false` en console (l.6) y `DAGS_ARE_PAUSED_AT_CREATION` parametrizable (l.13,17). **`make up` = base + dev + --profile sap** (Makefile:7-8,128-131) → el stack local "oficial" corre SIEMPRE sin rate-limit de login.
- `docker-compose.test.yml`: 2 mocks herméticos (mock_mcp 18010, mock_sap_api 18201-18204) servidos por scripts/mock_test_services.py; sin servicios reales. Correcto para `make test-hermetic` (Makefile:190-212).
- **Perfil SAP correcto**: 3 servicios bajo `profiles: ["sap"]`; E2E/release/stress/make up lo activan; smoke_test exige 8202/8203/8204 vivos (smoke_test.sh:92-100) — el perfil es de facto obligatorio para los gates.

**Huérfanos**: `REPLICON_USE_DEMO` y `REPLICON_MOCK_USER_COUNT` se escriben en .env (bootstrap.sh:223-224) pero ningún servicio del compose los consume (grep vacío). Volumen `wireguard_data` declarado en docker-compose.aws.yml:823 sin servicio que lo use.

---

## 2. Secrets / bootstrap

- **bootstrap.sh** (infra/bootstrap.sh): idempotente (sale si .env existe, l.9-12); genera con `openssl rand`: 5 claves maestras hex-32 (INTERNAL_API_KEY, JWT, SECURITY_CONTEXT_SIGNING_KEY, SUPERSET_SECRET_KEY, AIRFLOW_SECRET_KEY, l.19-23), 4 admin passwords + AGENT_RUNNER_TOKEN, 15 passwords de roles Postgres (l.36-67), 7 pair-keys de cartuchos (l.61-70), y **2 claves Fernet** via python stdlib: `VAULT_ENCRYPTION_KEY` (l.79-81) y `FIELD_ENCRYPTION_KEY` para PII SAP (l.87). `umask 077` (l.89). **Ningún default inseguro en los valores generados**; los flags de entorno escritos son prod-safe: `APP_ENV=production`, `ALLOW_RCE_TOOLS=false`, `RATE_LIMIT_ENABLED=true` (l.289-291).
- **bootstrap-keys.sh**: asegura **29 claves** (SECURITY_CONTEXT_SIGNING_KEY + **28 pares INTERNAL_API_KEY_X_TO_Y**, l.17-53) + 10 DB passwords (l.55-66); idempotente por-clave; `chmod 600` (l.75). La afirmación "~30 pares" es **aproximadamente correcta: 28 pares dedicados + 1 clave legacy compartida**.
- **gitignore**: `.env`, `*.env`, `infra/.env` explícito + backups + `*.pem`/`*.key` + tfstate/tfvars (.gitignore:12-45, 114-122), con whitelist solo de `.env.example`. Enforced por test: `tests/test_env_never_in_git.py` (existe). Tracked solo 3 `.env.example`.
- **Sweep de secretos cometidos**: `git grep` de AKIA/BEGIN PRIVATE KEY/sk-ant/password= → **cero secretos reales**. Solo fixtures de test con PEM dummy "unit-test" (cartridges/sap_successfactors/tests/test_saml_bearer_auth.py:29,122…; console-next/.../VaultConnectionsTable.test.tsx:131). CI usa valores `dummy_*_for_ci_only` (docker-image.yml:118-159) y una Fernet de forma válida solo-CI (l.133).
- **Credencial dev conocida**: hash bcrypt de `emmanuel@local.ai` cometido en infra/init_dev/15_local_dev_bootstrap.sql:9-11; password en claro `Admin123!` en e2e.yml:41-42 y release.yml:141-142. Mitigado: el seed solo corre si APP_ENV != production (compose l.47-53) y el archivo dice "LOCAL DEV ONLY".
- **AWS prod**: secretos NUNCA en repo — `scripts/aws-entrypoint.sh` exige ~56 secretos desde Secrets Manager (l.19-92) y escribe el .env del host con umask 077.

---

## 3. Superset

- **Versión 3.1.3 confirmada**: compose l.845, 925; aws compose l.453, 568.
- **Metastore**: runtime usa `omega_superset_meta@postgres:5432/superset` (compose:935); superset-init usa superusuario por diseño documentado y luego GRANTs al rol dedicado (compose:836-916).
- **Gold**: la conexión Studio→Superset apunta a `postgresql+psycopg2://omega_refinement_gold:…@postgres_gold:5433/modecissions_gold` vía `SUPERSET_GOLD_SQLALCHEMY_URI` (compose:193), consumida en console/app/routers/studio.py:2529. **Confirma postgres_gold/modecissions_gold con rol omega_refinement_gold.**
- **RLS**: no hay RLS a nivel Superset en superset_config.py; el aislamiento es **RLS nativo de Postgres en gold**: `infra/init_gold/35_gold_native_rls.sql` — `ALTER ROLE omega_refinement_gold NOBYPASSRLS` + `ENABLE/FORCE ROW LEVEL SECURITY` por tabla gold_* con política tenant/workspace por GUCs (l.7, 58-60). Verificado por beta_smoke.py (check_gold_db) y tests (tests/test_operational_native_rls.py en production_readiness.sh:203).
- **superset_config.py** (terraform/deploy/superset_config/, montado ro en ambos compose l.863/948): SECRET_KEY obligatorio de env, **rechaza fallback de URI** ("refusing superuser fallback", l.12-13), Talisman/CSRF/ratelimit default-on (l.17-19), CSP definido (l.32-44). Nota: cookies `SESSION_COOKIE_SECURE` default **false** en compose local (l.944) y .env generado (bootstrap.sh:164) — correcto para HTTP local, el config defaultea true si no se setea.

---

## 4. Airflow

- **2.10.5 confirmado**: `FROM apache/airflow:2.10.5` (infra/airflow/Dockerfile:1) + pip extra con `pip check`; imagen `mode-airflow:2.10.5-local` (compose:978).
- **LocalExecutor**: compose:1004, 1046, 1163. **Metastore en Postgres** (db `airflow`), runtime con rol `omega_airflow_meta` (1052, 1167); DAGs corren como `omega_airflow_dag` (1069-1070).
- **Init one-shot real**: crea DB si falta, `airflow db migrate`, GRANTs al rol meta, crea admin (compose:983-986), encadenado con `&&` → un fallo aborta y `airflow`/`airflow-scheduler` no arrancan (`service_completed_successfully`, 1130-1131/1224-1225). **Excepción**: `users create … || true` (l.986) traga errores reales de creación de admin.
- **8082→8080 confirmado** (l.1100). DAGs de cartuchos `:ro`; el padre RW (justificación l.1102-1108).

## 5. MailHog

- v1.0.1, SMTP 1025, UI 8025 (compose:421-425). **NO es dev-only**: vive en el compose base (default APP_ENV=production) y **también en el compose AWS de producción** (docker-compose.aws.yml:298-309), y console hace `depends_on: mailhog: service_healthy` en ambos (compose:240-241; aws:177). Terraform defaultea `email_provider="smtp"`, `smtp_host="mailhog"` (variables.tf:208-217).
- **Ruta SES en terraform existe y es real**: ses.tf (identidad de dominio, DKIM, MAIL FROM, MX/SPF/verificación Route53) + política IAM `app_ses` (iam.tf:93-110), pero **desactivada por default** (`ses_sender_domain=""`, variables.tf:225-228). Hasta configurar dominio, los emails de prod (invitaciones/reset con tokens) terminan en la UI no autenticada de MailHog accesible vía VPN.

---

## 6. Terraform — inventario y veredicto

`infra/terraform/infra/` (18 .tf, 2008 líneas): **REAL y coherente, no esqueleto.**

| Área | Archivo | Contenido |
|---|---|---|
| Backend | backend.tf:1-9 | S3 `modecissions-tfstate-us-east-1` + DynamoDB lock, encrypt |
| Provider | main.tf | AWS ~>5.0, default_tags, AMI Ubuntu 22.04 lookup |
| VPC | vpc.tf | 10.0.0.0/16, 2 públicas + 2 privadas (2ª privada solo para regla RDS 2-AZ), IGW, NAT gateway **o** NAT instance conmutables (`egress_mode`) |
| EC2 app | ec2_app.tf | m6i.xlarge privada, **IMDSv2 required**, gp3 150GB **cifrado**, user_data clona repo y arranca |
| VPN | ec2_vpn.tf + user_data/vpn.sh.tpl | WireGuard/wg-easy t3.nano, EIP, password hash |
| SG | security_groups.tf | ALB→app solo 8000/8001; VPN SG full hacia app; SSH/admin UI restringidos por CIDRs |
| HTTPS | public_https.tf (383 l.) | ALB + ACM + validación Route53 + target groups + listeners con redirect |
| WAF | waf.tf | rate-limit por IP + IpReputation + KnownBadInputs + CommonRuleSet (count/block conmutable) |
| SES | ses.tf | completo (ver §5) |
| Secrets | secretsmanager.tf | **58 secretos** `modecissions/*` (4 maestras + 28 pares + 15 roles DB + resto) |
| OIDC deploy | github_actions_deploy.tf | OIDC provider GitHub + rol restringido a `ssm:SendCommand` sobre **una instancia** + AWS-RunShellScript, sub claim por environment; default **deshabilitado** (variables.tf:332-335) |
| Budgets | budgets.tf | presupuesto mensual $50 default con umbrales 50/80/100% |
| Baseline | security_baseline.tf | AccessAnalyzer + S3 account public access block |
| S3 | s3.tf | lakehouse versionado + public access block + lifecycle IA |

- **Drift compose**: el EC2 **NO corre infra/docker-compose.yml** — corre `infra/terraform/deploy/docker-compose.aws.yml` (823 l., imágenes GHCR pinneadas a `IMAGE_TAG` required) + overlay `docker-compose.cartridges.yml`. Paridad vigilada por `tests/test_aws_compose_consistency.py` (existe, corre en release.yml:44-49). El AWS compose corrige cosas del local: red nombrada, logging acotado json-file 50m×5 (aws:10-14), restart unless-stopped en postgres (aws:43), superset bind `127.0.0.1:8088` (aws:593), vault sin puerto host. Pero **expone** refinement 8500, mailhog 1025/8025, airflow 8082 al host EC2 (gateado solo por SG).
- **Salesforce ausente del deploy AWS**: ni docker-compose.aws.yml ni el overlay definen `salesforce:` (lista de servicios verificada), pero el airflow AWS monta sus DAGs y setea `SALESFORCE_URL=http://salesforce:8205` (aws:712,729,785,800) → DAG/llamadas a host inexistente en prod.
- **Infra viva**: AWS-INVENTORY.md documenta cuenta `980921755079`, `i-00b8bd8b069146b8d` (app), `i-0ffd81fa030577b11` (VPN), EIP 34.239.194.46, ~$144/mes — **excede el budget default de $50** (variables.tf:147-150).

---

## 7. Workflows (.github/workflows) — solo lectura, 7 archivos, ninguno deshabilitado

| Workflow | Trigger | Jobs | Notas |
|---|---|---|---|
| docker-image.yml | push/PR main | `test` (5 suites + cartuchos + coverage), `compose-validate` (config -q con dummies), `build` (matriz **12 imágenes**) | permissions no declarados a nivel top (default token) |
| e2e.yml | push/PR main | stack completo dev+sap en runner, wait_for_health, `make smoke` ("34/34"), Playwright | credencial dev Admin123! (l.41-42); APP_ENV=development+RCE en CI efímero (l.28-31) |
| lint.yml | PR/push main | ruff pinned 0.6.9 + ESLint/Vitest/tsc + verificación de export estático | `contents: read` (l.14-15) |
| security.yml | PR/push/cron lunes | bandit (falla en MEDIUM+HIGH-confidence), pip-audit fail-closed con 2 ignores justificados (l.105-116), npm audit high | `contents: read` |
| release.yml | tags `v*.*.*` | validate (tests estáticos infra + `terraform validate` + 3 configs compose) → full-stack gate (smoke+e2e+acceptance+production-readiness; stress skip documentado en tags beta l.182-184) → push GHCR 12 imágenes | `packages: write` |
| deploy-aws.yml | **workflow_dispatch** | OIDC AssumeRole (`id-token: write`), valida tag inmutable `v*`, SSM RunShellScript → update.sh → healthz/readyz(+require_data) local y público | referencia infra viva: ALB `modecissions-public-255609366.us-east-1.elb.amazonaws.com` default (l.43); secrets/vars AWS_DEPLOY_ROLE_ARN, AWS_APP_INSTANCE_ID (l.41-42); environment `production` (l.36) |
| monitor-aws-health.yml | cron */30 + dispatch | monitor_health_once.sh contra la ALB pública con `require_data=1` | misma URL viva (l.21) |

---

## 8. Realismo de scripts (~33)

**Reales (con aserciones de comportamiento):**
- `smoke_test.sh` (338 l.): **no es teatro** — 34+ checks: /healthz de 5 servicios + 6 cartuchos, clasificación honesta de auth-gate 000/401/403/5xx (l.45-57), detección de restart-loops (l.112), conteo de tablas migradas (l.210-217), **aserciones positivas y negativas de GRANT/REVOKE** (omega_console DENIED en vault_entries l.242-249; omega_mcp_infra DENIED en users/tenants/decisions l.256-265; roles v1.38 DENIED en users l.287-296; checks positivos anti-over-revoke l.298-326), probes autenticadas a /mcp/tools con conteo de tools (l.179-204).
- `beta_smoke.py` (18.6KB): identidad de release (VERSION beta + tag exacto opcional), compose config, superficies HTTP, DB operacional, **gold RLS**, writeback bloqueado; escribe evidencia en docs/release-evidence/beta-smoke (l.30-31, métodos l.93-413).
- `wait_for_health.sh`: health de contenedor + HTTP + restart-loop + racha de estabilidad (streak=2) (l.13-92).
- `production_readiness.sh`: gate compuesto — acceptance para calentar Bronze/Silver/Gold, `/readyz?require_data=1` con `ok=true` parseado (l.92-104), **login real a Superset API asertando access_token** (l.113-134), probe LLM vivo opcional, pytest de regresión de scopes (l.196-211), make test+smoke+e2e+stress (l.285-333). Modo remoto más delgado (healthz/readyz/superset/E2E opcional, l.267-283).
- `apply_db_migrations.sh`: runner real con ledger `schema_migrations`, replay idempotente para init e init_gold, ON_ERROR_STOP.
- `monitor_health_once.sh`: 3 endpoints con verificación JSON `ok==true` (no solo HTTP 200).
- `preflight.sh`: daemon docker, compose v2, validez del compose, .env, capacidad Fernet del Python host, puertos ocupados.
- `run_stress.sh` + stress_summary/data_integrity_audit (48 checks/función), rotate_fernet_key.sh, reconcile_db_passwords.sh, run-e2e.sh: consistentes con el patrón (verificación por muestreo).

**Caveat general**: casi todos los gates apuntan a localhost; el modo "aws" depende de `OMEGA_PRODUCTION_READINESS_REMOTE=1` que ejecuta un subconjunto.

---

## 9. Deriva de versión

| Fuente | Valor | Evidencia |
|---|---|---|
| VERSION @ HEAD | **1.45.68-beta** | VERSION:1; commit bab2a4f "bump beta release to 1.45.68" |
| Checklist v1 (actual) | 1.45.68-beta | docs/release-checklist-v1.md:5 |
| Evidencia/diagramas "v1.45.3-beta" | **stale** | docs/release-evidence/main-9a39a30…md:6 ("Version at base: 1.45.3-beta") |
| Último deploy AWS evidenciado | **v1.45.30-beta** | docs/release-evidence/sf-gold-foundation/aws-pr270.md:19-23 (imágenes GHCR v1.45.30-beta healthy) |
| Tags git en el clon | ninguno | `git tag` vacío |
| AWS-INVENTORY | actualizado 2026-05-02 | infra/terraform/AWS-INVENTORY.md:7 |

Veredicto: el claim v1.45.3-beta proviene de evidencia congelada; el repo va por 1.45.68-beta y **prod desplegado va ~38 parches por detrás** (v1.45.30). beta_smoke.py:93-127 vigila la alineación tag↔VERSION solo cuando se corre desde un tag.

---

## 10. Hallazgos P0–P3

| ID | Sev | Hallazgo | Evidencia | Estado |
|---|---|---|---|---|
| INF-01 | **P1** | MailHog en compose de PRODUCCIÓN: console depende de él y SMTP default apunta ahí; tokens de invite/reset legibles en UI 8025 sin auth (alcance VPN/SG). SES existe pero default-off | docker-compose.aws.yml:298-309,138,177; variables.tf:208-217,225-228; compose:199,240-241 | ABIERTO |
| INF-02 | **P1** | Sin `restart:` en postgres, postgres_gold, redis, minio, refinement en compose base → tras reboot del host la base no vuelve y el resto crash-loopea | compose:5-34,56-74,76-87,89-116,309-364 (aws lo corrige: aws:43) | ABIERTO (solo local) |
| INF-03 | **P1** | Red plana única, sin segmentación: cualquier cartucho alcanza postgres:5432 superuser, vault:8300, etc. (mitigado por pair-keys/roles, no por red) | compose sin `networks:` (fin de archivo 1237-1240); aws:17 una sola bridge | ABIERTO |
| INF-04 | **P1** | Salesforce cartridge NO desplegado en AWS pero airflow prod monta sus DAGs y apunta SALESFORCE_URL a host inexistente → fallos en runtime prod / deriva local↔prod | deploy/docker-compose.aws.yml:712,729,785,800; docker-compose.cartridges.yml (sin servicio salesforce) | ABIERTO |
| INF-05 | P2 | Exposición de puertos internos al host en compose base: postgres 15432, gold 15433, minio 9000/9001, vault 8300, mcp-infra 8010, refinement 8500, mailhog 1025/8025 | compose:23-24,64-65,100-102,404-405,808-809,345-346,423-425 | ABIERTO (aceptable en laptop; riesgo en host compartido) |
| INF-06 | P2 | `make up` (camino oficial del runbook) incluye overlay dev → `RATE_LIMIT_ENABLED=false` siempre en local "full" | Makefile:7-8,128-131; docker-compose.dev.yml:6 | ABIERTO (intencional, documentado) |
| INF-07 | P2 | mcp-infra monta `../airflow/dags` RW ("write DAGs directly") y el scheduler ejecuta ese código → escalada potencial mcp-infra→ejecución en Airflow; guard solo en capa de tool (APP_ENV+ALLOW_RCE_TOOLS) | compose:811,1102-1108 | ABIERTO (riesgo aceptado documentado) |
| INF-08 | P2 | postgres_dev_seed puede fallar en silencio: restart "no" y ningún servicio depende de su éxito | compose:36-54 | ABIERTO (bajo impacto: solo dev) |
| INF-09 | P2 | airflow-init: `airflow users create … \|\| true` traga errores reales de creación del admin | compose:986 | ABIERTO |
| INF-10 | P2 | Defaults dummy que sobreviven a prod: `OUTLOOK_APP_PASSWORD:-dummy-local-password` en webserver y scheduler; SMTP_FROM noreply@modecissions.local | compose:1078,1083,1179,1184,203 | ABIERTO |
| INF-11 | P2 | user_data usa `StrictHostKeyChecking no` para el clone inicial (MITM en primer boot); el deploy posterior sí usa ssh-keyscan | user_data/app.sh.tpl:38-43; deploy-aws.yml:146 | ABIERTO |
| INF-12 | P2 | Credencial dev cometida (hash bcrypt + password en claro en workflows) — correctamente gateada a APP_ENV!=production | init_dev/15_local_dev_bootstrap.sql:9-11; e2e.yml:41-42; compose:47-53 | MITIGADO |
| INF-13 | P3 | Cartuchos (6) embarcan build-essential+curl en imagen final (sin multi-stage); mcp-infra sí es multi-stage | cartridges/*/Dockerfile (RUN apt-get install build-essential); mcp-infra/Dockerfile:1-38 | ABIERTO |
| INF-14 | P3 | Sin límites de recursos salvo refinement; superset+airflow+19 servicios sin caps en 16GB | compose:314-315 únicos | ABIERTO |
| INF-15 | P3 | Config muerta: REPLICON_USE_DEMO/REPLICON_MOCK_USER_COUNT generados pero no consumidos; volumen wireguard_data sin servicio | bootstrap.sh:223-224; aws:823 | ABIERTO |
| INF-16 | P3 | Budget default $50/mes vs costo real documentado ~$144/mes → alarmas permanentes salvo override en tfvars | variables.tf:147-150; AWS-INVENTORY.md (~$144) | ABIERTO |
| INF-17 | P3 | Imágenes base pinneadas por tag, no por digest (python:3.12-slim, redis:7-alpine, pg15); MinIO y Superset/Airflow sí tienen versión exacta | Dockerfiles; compose:6,77,94,845 | ABIERTO |
| INF-18 | OK | Sin secretos cometidos; .env multi-blindado en gitignore + test; bootstrap criptográficamente sano (openssl/urandom, umask 077) | §2 | VERDE |
| INF-19 | OK | APP_ENV fail-closed: default `production` en compose y en código | compose:138 et al.; console/app/security.py:11-17; tests/test_app_env_safe_default.py | VERDE |
| INF-20 | OK | Infra viva real detrás de los workflows (ALB DNS, instance IDs, OIDC+SSM least-privilege) | deploy-aws.yml:43; AWS-INVENTORY.md; github_actions_deploy.tf | VERDE |

---

### Veredicto global
La capa infra es **sustancialmente real**: compose disciplinado (healthchecks reales, depends_on condicionados, roles DB por servicio, init one-shots encadenados con fail-propagation), terraform completo y desplegado de verdad, CI con gates que ejecutan el stack completo, y scripts de smoke/readiness con aserciones de comportamiento (no curl-200 teatral). Las grietas principales son operativas, no cosméticas: MailHog como mail de prod por default, ausencia de restart en datastores del compose local, red plana, y la deriva local↔AWS (salesforce ausente, dos composes paralelos, prod 38 parches detrás del repo).
