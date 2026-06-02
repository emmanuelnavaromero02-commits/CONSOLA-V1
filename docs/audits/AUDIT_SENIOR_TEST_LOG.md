# AUDIT_SENIOR_TEST_LOG — CONSOLA-BETA / OMEGA

Registro de TODOS los comandos ejecutados durante la auditoría: comando, cwd,
resultado, salida relevante, y motivo cuando no pudo correr. Entorno:
contenedor efímero, **sin Docker daemon** (`/var/run/docker.sock` ausente),
**sin `terraform`**. Python del sistema 3.11.15 + 3.12.3 disponibles.
Rama: `claude/beautiful-maxwell-KTDgM` @ `22c37f2`.

---

## 0. Entorno y herramientas

| Comando | cwd | Resultado |
|---|---|---|
| `python --version` | / | Python 3.11.15 |
| `/usr/bin/python3.12 --version` | / | Python 3.12.3 (usado para el rerun autoritativo) |
| `node --version` / `npm --version` | / | v22.22.2 / 10.9.7 |
| `ruff --version` | / | ruff 0.15.8 |
| `which bandit pip-audit terraform` | / | bandit/pip-audit ❌ ausentes (instalados via pip) · **terraform ❌ ausente** |
| `rg --version` | / | ripgrep 14.1.0 |
| `docker --version` | / | Docker 29.3.1 (CLI presente, **daemon NO**) |

**Setup de venvs** (el intérprete del sistema no pudo instalar deps por conflicto
`PyYAML` instalado por Debian sin RECORD):
- `/tmp/auditvenv` (Python 3.11) — `pip install -r {tests,console,refinement,vault,workspace,mcp-infra}/requirements.txt bandit pip-audit pytest` → exit 0.
- `/tmp/venv312` (Python 3.12) — idem → exit 0. **Usado para los resultados autoritativos.**

---

## 1. Lint estático

| # | Comando | cwd | Resultado |
|---|---|---|---|
| 1 | `ruff check .` | repo | ✅ **All checks passed!** (exit 0) |

> Nota: `ruff.toml` usa `target-version = "py312"`. Por eso ruff NO marca el
> f-string PEP 701 de `console/app/services/control_room/api.py:305`, que sí
> rompe en Python 3.11 (ver §5).

---

## 2. SAST — Bandit

| # | Comando | Resultado |
|---|---|---|
| 2 | `bandit -r console workspace vault refinement mcp-infra cartridges --severity-level medium --confidence-level high` | ✅ **0 hallazgos** |
| 3 | `bandit -r console workspace vault refinement mcp-infra cartridges airflow scripts -f json` (scan completo) | 2.359 hallazgos: **1 HIGH, 129 MEDIUM, 2229 LOW** |

**Desglose del scan completo:**
- `B101 assert_used` (LOW): 2.041 — asserts en tests, irrelevante.
- `B608 hardcoded_sql_expressions` (MEDIUM): **129** — string-SQL. Concentrado en `refinement/app/duckdb_engine.py`, `mcp-infra/app/main.py`, `console/app/main.py`. **Triaje:** mayormente interpolación de identificadores validados (`SAFE_IDENTIFIER_RE`, `validate_safe_identifier`) o nombres de schema/tabla server-side; no se confirmó SQLi por input de usuario. Clase a parametrizar donde sea posible.
- `B110 try_except_pass` (LOW): 98 — relacionado con P2-OBS-001/P3-OBS-001.
- `B105 hardcoded_password_string` (LOW): 71 — fixtures/constantes de test (spot-check: no secretos reales).
- `B613 trojansource` (**HIGH**): 1 — `console/app/services/lessons_service.py:449`. **REFUTADO**: es la tupla defensiva `_INVISIBLE_CHARS` anti-prompt-injection (el código comenta `<- the attack`). Falso positivo. Recomendado `# nosec B613`.
- `B113 request_without_timeout` (MEDIUM): 1 — `console/app/routers/pages.py:151` (proxy streaming `timeout=None`) → P3-OBS-002.
- `B104 hardcoded_bind_all_interfaces` (MEDIUM): 1 — `scripts/mock_test_services.py:89` (mock de test).

---

## 3. Supply-chain — pip-audit

| # | Comando | Resultado |
|---|---|---|
| 4 | `pip-audit -r <req> --vulnerability-service=pypi` × 14 requirements.txt | ✅ **No known vulnerabilities found** en los 14 |

Archivos auditados: `cartridges/{hubspot,replicon,salesforce,sap_hcm,sap_s4hana,sap_successfactors}/requirements.txt`, `console`, `infra/airflow`, `mcp-infra`, `refinement`, `tests`, `tests/stress`, `vault`, `workspace`. **0 CVEs conocidos.**

---

## 4. Pytest — Python 3.12 (AUTORITATIVO)

cwd = repo. `OMEGA_ENABLE_LIVE_STACK_TESTS=0`. Intérprete `/tmp/venv312/bin/pytest`.

| # | Comando | Resultado | Duración |
|---|---|---|---|
| 5 | `pytest -ra -q tests/` | ✅ **2174 passed, 66 skipped** (exit 0) | 128.9s |
| 6 | `PYTHONPATH=console pytest -ra -q console/tests/` | ⚠️ **708 passed, 1 error** | 40.3s |
| 7 | `PYTHONPATH=. pytest -ra -q refinement/tests/` | ✅ **122 passed** | 2.1s |
| 8 | `PYTHONPATH=vault pytest -ra -q vault/tests/` | ✅ **32 passed** | 1.0s |
| 9 | `PYTHONPATH=workspace pytest -ra -q workspace/tests/` | ✅ **33 passed** | 2.9s |
| 10 | `pytest -q cartridges` | ✅ **203 passed** | 6.6s |

**TOTAL: ~3.272 passed, 66 skipped, 1 error.**

- Los **66 skipped** son tests **opt-in de stack vivo** (`tests/e2e/*`, `tests/test_e2e_*`, `tests/test_security_negative_v141.py` [36], probes de Postgres/MinIO): se saltan porque el stack no está levantado (sin Docker daemon). Correctamente gateados.
- El **1 error** es `console/tests/test_cross_tenant_api_isolation.py::test_postgres_rls_blocks_workspace_b_rows_from_workspace_a`: intenta `docker run` un `pgvector/pgvector:pg15` real y falla con `failed to connect to the docker API at unix:///var/run/docker.sock`. **Limitación de entorno, no del código.** (Hallazgo colateral: este test crea la policy RLS *dentro del test* → confirma P0-RLS-002, que producción no la despliega.)

---

## 5. Pytest — Python 3.11 (comparativo; documenta el artefacto de versión)

| # | Comando | Resultado |
|---|---|---|
| 11 | `pytest -ra -q tests/` (3.11) | ⚠️ 8 failed, 2096 passed, 66 skipped, **70 errors** |
| 12 | `PYTHONPATH=console pytest console/tests/` (3.11) | ❌ **3 errores de colección** — `SyntaxError` |

**Causa raíz:** `console/app/services/control_room/api.py:305` usa un f-string con
comillas dobles anidadas (PEP 701, válido solo en Python 3.12+):
```python
f"WHERE {source.entity_id_field} = '{str(entity_id or label).replace("'", "''")}' "
```
- `python3.11 -c "import ast; ast.parse(open('.../control_room/api.py').read())"` → **SyntaxError: unterminated string literal (line 305)**.
- `python3.12 -c "import ast; ast.parse(...)"` → **OK**.

**Conclusión:** los 8 failed + 70 errors de la corrida 3.11 son **artefacto de correr
en la versión equivocada**. En Python 3.12 (la versión del proyecto: Dockerfiles +
5 workflows CI + `ruff.toml`) **todo pasa**. Hallazgo P2-PORT-001: el repo perdió
soporte 3.11 sin que ninguna barra lo marque.

---

## 6. Frontend — npm (console-next)

cwd = repo. Node v22.22.2.

| # | Comando | Resultado |
|---|---|---|
| 13 | `npm --prefix console-next ci` | ✅ exit 0 — **found 0 vulnerabilities** |
| 14 | `npm --prefix console-next run lint` (eslint) | ✅ exit 0 — **0 errors, 1 warning** (`control-room/page.tsx:629` var `refreshing` sin usar) |
| 15 | `npm --prefix console-next run typecheck` (`tsc --noEmit`) | ✅ exit 0 — sin errores de tipos |
| 16 | `npm --prefix console-next run test` (vitest) | ✅ **8 files, 27 tests passed** (1.19s) |
| 17 | `npm --prefix console-next run verify:static` | ✅ exit 0 — export estático coincide con el committeado (sin drift) |
| 18 | `npm --prefix console-next audit --audit-level=high` | ✅ **found 0 vulnerabilities** |

> `verify:static` verde confirma que el mecanismo export→`console/app/static/console-next`
> funciona y no hay drift local; P3-DRIFT-001 recomienda **enforzarlo en CI** (hoy no es required check).

---

## 7. E2E — npm (tests-e2e) — inventario, sin stack vivo

| # | Comando | Resultado |
|---|---|---|
| 19 | `npm --prefix tests-e2e ci` | ✅ exit 0 — **found 0 vulnerabilities** |
| 20 | `npm --prefix tests-e2e audit --audit-level=high` | ✅ **found 0 vulnerabilities** |
| 21 | `npm --prefix tests-e2e run test:list` | ✅ **Total: 360 tests in 18 files** (RC=0) |
| 22 | `npm --prefix tests-e2e test` (Playwright real) | ❌ **NO EJECUTADO** — requiere stack vivo en `:8000` (sin Docker daemon) |

Specs presentes incluyen `12-control-room.spec.ts` (cockpit en `:8000` sin llamadas a `:3000`, item→decision→approval→audit, `stopped_before_writeback`), `11-copilot-deep.spec.ts`, `09-ux-mobile.spec.ts` (a11y/contraste/touch). El inventario existe y está bien estructurado; **no se pudo correr en vivo**.

---

## 8. Infra — docker compose / terraform

| # | Comando | Resultado |
|---|---|---|
| 23 | `docker compose -f infra/docker-compose.test.yml config -q` | ✅ válido |
| 24 | `docker compose -f infra/docker-compose.yml config -q` | ⚠️ falla: `POSTGRES_PASSWORD is required` (esperado sin `infra/.env`) |
| 25 | `docker compose -f infra/docker-compose.yml --profile sap config -q` | ⚠️ falla: `AIRFLOW_ADMIN_PASSWORD is required` (esperado) |
| 26 | `docker compose -f infra/terraform/deploy/docker-compose.aws.yml config -q` | ⚠️ falla: `IMAGE_TAG is required` (esperado) |
| 27 | `terraform fmt/init/validate` | ❌ **NO EJECUTADO** — `terraform` no instalado. Revisión estática de `.tf`. |

> Las fallas de `config -q` (24-26) son por **interpolación de env vars ausentes**,
> no por estructura inválida. La estructura, healthchecks, `depends_on`, non-root
> y secrets se revisaron estáticamente (ver hallazgos INFRA/ENV/DIVERGE).

---

## 9. Gates que NO se pudieron correr (y por qué)

| Gate | Motivo exacto | Alternativa |
|---|---|---|
| `make up` / `make smoke` / `make e2e` / `make test-hermetic` / `make acceptance` / `make verify-release` | **Sin Docker daemon** (`/var/run/docker.sock` ausente) + sin `infra/.env` | Pytest unitario en venv; revisión estática de compose/scripts |
| `terraform fmt -check` / `init -backend=false` / `validate` | `terraform` no instalado | Lectura de `iam.tf`/`s3.tf`/`security_groups.tf`/`secretsmanager.tf` |
| `npm --prefix tests-e2e test` (Playwright) | Requiere stack vivo `:8000` | `test:list` (inventario de 360 specs) |
| `test_cross_tenant_api_isolation.py` (RLS real con pgvector) | Requiere Docker daemon | Verificación estática de esquema (confirmó P0-RLS-002) |

**Ningún fallo se ocultó por falta de dependencias.** Donde un control no pudo
probarse en vivo, se trazó estáticamente con `file:line` y se marcó
`needs-runtime-validation` en `AUDIT_SENIOR_FINDINGS.json` donde corresponde.

---

## 10. Verificaciones directas del lead (lectura `file:line`, joyas de la corona)

| Verificación | Resultado |
|---|---|
| RLS read-path vs bypasses de forma (casing/comillas/comentario/CTE/UNION/subquery/stacked) | Probado en vivo contra sqlglot 25.16.1 por el carril Data: **sin bypass de forma**; el riesgo es arquitectónico (P0-RLS-001/002, P1-RLS-003). |
| `validate_kb_sql` en los 6 cartridges | Confirmado idéntico/estricto; SuccessFactors el más estricto → **P1-SQL-001 REFUTADO**. |
| `CONTROL_ROOM_SECURITY_HEADERS` (`main.py:876-888`) | `script-src 'self'` → **P1-CR-CSP-001 REFUTADO**. |
| `materialize` gold (`duckdb_engine.py:1015-1109`) + `_ensure_scope_columns` (`:522-543`) | Tabla compartida `gold_{name}`, scope solo se añade no se pisa → **P0-RLS-001 confirmado**. |
| `/api/datasets/save` + `/datasets/{name}/refresh` (`data.py:76,109`) + `permissions.py:152-157` | `workspace_admin` tiene `datasets.write` → cadena de explotación P0-RLS-001 confirmada. |
| Write-back SAP (`execution.py:2236-2248`, `sap_hcm_adapter.py:43`, `09_demo_beta.md:21`) | Flag OFF→409 por default, pero adapter real existe → **P1-CR-001 confirmado/actualizado**. |
| `users.role` (`infra/init/05_users.sql:9` NOT NULL DEFAULT 'user') | Refuta explotabilidad de la escalada de settings → **P3-AUTH-001 (latente, no explotable)**. |
| RLS Postgres repo-wide (`rg CREATE POLICY/ROW LEVEL SECURITY`) | Único match en un test → **P0-RLS-002 confirmado**. |
