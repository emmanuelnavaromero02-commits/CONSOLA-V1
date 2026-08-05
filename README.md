# CONSOLA-V1 / OMEGA

OMEGA es una consola enterprise privada para operar integraciones,
datasets, Vault, MCP/Copilot, Airflow, Superset y experiencias de consola
servidas por FastAPI + Next static export.

Estado de release actual:

- Beta privada/controlada: SI, con stack local validable.
- v1.0 publica enterprise: NO todavia.
- Version actual: ver `VERSION`.

No promociones este repo como v1.0 publica hasta que el checklist de
`docs/release-checklist-v1.md` y los gates P2 esten verdes sin skips.

## Requisitos

| Herramienta | Minimo |
|---|---|
| Docker Engine | 24+ con daemon corriendo |
| Docker Compose | v2.20+ |
| GNU make | 4+ |
| Python 3 | stdlib funcional para generar Fernet keys |
| Node.js / npm | version compatible con `console-next/package.json` |
| Recursos locales | 16 GB RAM libres / 30 GB disco libre |

## Primer Arranque Local

Desde la raiz del repo:

```bash
make preflight
make up
bash scripts/wait_for_health.sh
```

`make up` ejecuta `make bootstrap-env`, genera `infra/.env` si no existe,
aplica las pair keys con `infra/bootstrap-keys.sh`, crea `data/lakehouse`
y levanta el stack completo con perfil SAP usando:

```bash
docker compose -f infra/docker-compose.yml -f infra/docker-compose.dev.yml --profile sap up --build -d
```

El compose base mantiene `RATE_LIMIT_ENABLED` activo. El override
`infra/docker-compose.dev.yml` solo lo apaga para desarrollo/E2E local.

## Que Ejecutar Segun El Caso

Tres niveles, de mas barato a mas caro. No sustituyen uno al otro.

| Nivel | Comando | Necesita stack | Duracion | Para que sirve |
|---|---|---|---|---|
| Baseline | `make baseline-smoke` | No | < 1 min | El checkout es coherente: VERSION, comandos, YAML, ruff |
| Rapido | `make smoke` | Si | ~2 min | El stack levantado responde de verdad |
| Gate | `make test` + `make beta-smoke` + `make e2e` | Si | horas | Puerta de release |

`make baseline-smoke` es lo primero tras clonar o cambiar de rama: no
levanta Docker y solo detecta drift del baseline. No es una puerta de
producto — nunca reemplaza a `make test` ni a `make beta-smoke`.

El gate completo esta en `make verify-release`, que reproduce lo que CI
ejecuta (`.github/workflows/lint.yml` y `security.yml`) mas el stack real.
El `pip-audit` de ambos es fail-closed y sin supresiones: si divergen, CI
es la referencia.

Riesgos y deuda vigentes del baseline: `docs/baseline.md`.

## Validacion Rapida

Con el stack arriba:

```bash
make smoke
make e2e
make acceptance
OMEGA_PRODUCTION_READINESS_SKIP_STRESS=1 make production-readiness
```

Smoke esperado en la beta actual: `47/47 checks passed`.

E2E esperado en la beta actual: alrededor de `357 passed, 3 skipped`
segun el estado de tests del commit.

Acceptance cubre superficies de Console, Studio/Copilot/Semantic/Knowledge
y extraccion HubSpot fake upstream hasta Bronze/Silver/Gold.

## Tests de Desarrollo

```bash
make test
npm --prefix console-next run typecheck
npm --prefix console-next run lint
npm --prefix console-next run test
npm --prefix console-next run test:coverage
npm --prefix console-next run build
```

Auditorias de dependencias y seguridad:

```bash
make security-scan
.venv/bin/bandit -r console workspace vault refinement mcp-infra cartridges --severity-level medium --confidence-level high
.venv/bin/pip-audit
npm --prefix console-next audit
npm --prefix tests-e2e audit --audit-level=high
```

## URLs Locales

| Servicio | URL |
|---|---|
| Console / Next static export | http://localhost:8000 |
| Workspace | http://localhost:8001 |
| MCP Infra | http://localhost:8010 |
| Airflow | http://localhost:8082 |
| Superset | http://localhost:8088 |
| Vault | http://localhost:8300 |
| Refinement | http://localhost:8500 |
| Replicon | http://localhost:8201 |
| HubSpot | http://localhost:8210 |
| SAP HCM | http://localhost:8202 |
| SAP SuccessFactors | http://localhost:8203 |
| SAP S/4HANA | http://localhost:8204 |

Health/readiness basicos:

```bash
curl -fsS http://localhost:8000/healthz
curl -fsS 'http://localhost:8000/readyz?require_data=1'
curl -fsS http://localhost:8010/readyz
curl -fsS http://localhost:8500/readyz
```

## Reset y Reparacion Local

Para un reset destructivo solo de desarrollo local:

```bash
make nuke CONFIRM=NUKE NUKE_SCOPE=local-dev
make up
```

`make nuke` borra volumenes Docker locales. Never use it for AWS,
staging, production, or any host containing customer data.

Si hay volumenes locales viejos con passwords desincronizados o roles de
Postgres desfasados:

```bash
make repair-local-stack
```

Si Superset local tiene metastore cifrado con una `SUPERSET_SECRET_KEY`
anterior, la reparacion es explicitamente opt-in:

```bash
CONFIRM_SUPERSET_METASTORE_REPAIR=LOCAL_SUPERSET_REPAIR make repair-local-stack
```

En produccion no ejecutes limpieza destructiva ni resets de volumenes. El
flujo correcto es backup/restore, rotacion controlada de secretos y rollback
por tag inmutable.

## Seguridad y Fronteras Importantes

- Gold/pggold se consulta por Refinement para aplicar el guard RLS por AST
  con `sqlglot` y default-deny.
- MCP/Copilot es read-only por defecto; mutaciones requieren contexto admin,
  approval y auditoria.
- `ALLOW_RCE_TOOLS=false` bloquea herramientas RCE-like en produccion.
- JWT, CSRF, Vault scope, pair keys y secrets fail-closed ya son parte del
  contrato de seguridad.
- `style-src 'unsafe-inline'` sigue como excepcion formal acotada para
  estilos legacy; `script-src` no debe usar inline.

Ver detalles en `SECURITY.md` y `docs/security/`.

## Lo Que Esta Beta No Promete

- No hay v1.0 publica enterprise.
- No hay stress/load completo sin skip validado como gate definitivo.
- No hay live LLM probe obligatorio sin keys.
- No hay integraciones reales con credenciales SAP/Salesforce/HubSpot/
  Replicon ejecutadas para todos los cartuchos en este repo local.
- No hay despliegue AWS/HTTPS demostrado por este README.
- No hay write-back externo universal a SAP/Replicon/Salesforce.

Si falta Docker, AWS, LLM keys o credenciales externas, deja el script/test
gated listo y marca `BLOCKED`; no simules `DONE`.

## Runbooks

- `docs/runbook/01_arrancar_desde_cero.md`: arranque limpio, dos fases,
  Postgres primero, healthchecks y fixes locales.
- `docs/runbook/02_primer_tenant.md`: bootstrap admin y primer workspace.
- `docs/runbook/03_configurar_replicon.md`: credenciales Replicon en Vault.
- `docs/runbook/04_configurar_sap.md`: SAP HCM, S/4HANA y SuccessFactors.
- `docs/runbook/05_rotar_secretos.md`: rotacion de claves y roles.
- `docs/runbook/06_backup_restore.md`: backup/restore.
- `docs/runbook/07_debug_fallos.md`: debugging operativo.
- `docs/runbook/08_usar_copiloto.md`: Copilot y approval gate.
- `docs/runbook/09_demo_beta.md`: checklist de demo/beta controlada.
- `docs/runbook/10_v1_public_https.md`: HTTPS publico.
- `docs/runbook/11_release_stabilization.md`: estabilizacion y rollback.
- `docs/runbook/12_scope_hardening.md`: tenant/workspace scope.

El changelog de hardening que antes estaba en el README se archivo en
`docs/audits/security-phase-hardening-changelog.md`.
