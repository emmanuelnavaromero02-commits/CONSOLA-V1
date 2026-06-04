# Security Phase Hardening Changelog

Este documento conserva el contenido historico que antes ocupaba el inicio
de `README.md`. El README principal ahora es una guia de onboarding y
arranque reproducible.

## Phase 1 - Residual Closures

1. **RLS en duckdb_engine.py**: la revision historica describia el cierre
   del bypass de `pggold`; el estado actual usa guard AST con `sqlglot` y
   default-deny en `refinement/app/duckdb_engine.py`.
2. **vault/secrets.yaml**: la interpolacion Bash-style (`${VAR:-""}`) fue
   reemplazada por referencias estandar `${VAR}` para resolucion correcta.
3. **Credenciales hardcodeadas**: los defaults inseguros fueron reemplazados
   por mapeos de entorno seguros.
4. **Workspace API**: `/api/data/{dataset}` pasa contexto de usuario para que
   Refinement aplique scope multi-tenant.
5. **Airflow Connections**: las conexiones se inyectan via Vault y no se
   exponen raw en templates.

## Phase 2 - Quick Wins

1. **Security headers**: `X-Frame-Options`, `Strict-Transport-Security`,
   `Content-Security-Policy` y `X-Content-Type-Options` se inyectan en
   `console` y `workspace`.
2. **Rate limiting**: los endpoints criticos tienen rate limiting; el compose
   base ya no lo desactiva, y el override dev/E2E es el unico que lo apaga.
3. **Higiene Docker**: los Dockerfiles core y cartuchos corren con usuario
   no-root y healthchecks.

## Archivos historicamente modificados

- `refinement/app/duckdb_engine.py`
- `vault/secrets.yaml`
- `console/app/main.py`
- `workspace/app/main.py`
- `console/Dockerfile`
- `mcp-infra/Dockerfile`
- `refinement/Dockerfile`
- `vault/Dockerfile`
- `workspace/Dockerfile`
- `infra/docker-compose.yml`
- `.github/workflows/docker-image.yml`

## Estado CSP historico

El README antiguo declaraba como abierto `script-src 'unsafe-inline'` en
`console` y `workspace`. Ese texto quedo obsoleto: el contrato actual exige
`script-src 'self'` sin inline. La excepcion que sigue documentada es
`style-src 'unsafe-inline'` para estilos legacy, acotada en `SECURITY.md`.

## Nota de trazabilidad

Este archivo es historico. Para operar el repo usa `README.md` y
`docs/runbook/`; para decisiones de seguridad actuales usa `SECURITY.md` y
`docs/security/`.
