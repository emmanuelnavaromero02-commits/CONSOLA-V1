# F3 — Backlog verificado (P0/P1 + aislamiento multi-tenant)

Base: código en `34504a10` (= origin/main = v1.45.222-beta). Verificado por 3 revisores independientes (read-only), evidencia con path:line. El patrón **correcto ya existe en el repo** (`cartridges/sap_successfactors`) — todos los arreglos son **reusar ese patrón**, no construir marcos nuevos.

## Hallazgo tranquilizador
El aislamiento del **núcleo** (console/refinement/workspace/vault/mcp-infra) es **sólido y fail-closed**: cada servicio conecta con su propio rol Postgres `NOBYPASSRLS`, GUCs transaction-local, RLS forzado. **No se halló ninguna ruta P0/P1 de cross-tenant en el núcleo.** Los P1 reales están en los **conectores**.

## P1 — a cerrar en F3 (aislamiento entre clientes)

| ID | Título | Severidad | Dónde | Arreglo (reusar patrón) |
|----|--------|-----------|-------|--------------------------|
| F3-1 | Credencial upstream resuelta SIN el contexto firmado → un tenant puede ingerir datos de OTRA empresa | **P1 (roza P0)** | `hubspot/…/extraction_service.py:73`, `salesforce:91`, `sap_hcm:91`, `sap_s4hana:87` (cliente creado sin `security_context`) | Serializar `config["security_context"]` a JSON y pasarlo al constructor del cliente, como `sap_successfactors/…/extraction_service.py:282-284,333`; incluir el scope verificado en la clave de `_CONNECTION_CACHE` |
| F3-2 | Scope tenant/workspace tomado del **body sin firmar** (y hasta lo sobre-escribe) | **P1** | `banxico/…/routes_skills.py:80-82`, `inegi:84-86`, `sec_edgar:87-89,112-114` (no tienen `request_context.py`) | Copiar `request_context.py` de un par; derivar scope SOLO del contexto firmado y verificado (403 si no hay firma); borrar la precedencia de `body.get("tenant_id")` |
| F3-3 | Watermark tomado de `os.environ` (nunca seteado) → todos los tenants colisionan en "platform" → **un cliente hace que otro se salte sus propios datos** (pérdida silenciosa) | **P1** | `hubspot/…/watermark_service.py:10-13`, `salesforce`, `sap_hcm`, `sap_s4hana`, `replicon` (mismo `_scope_ids()`) | Usar `scope_values()` del contexto verificado primero, como `sap_successfactors/…/watermark_service.py:11-19` |
| F3-4 | Replicon scoped rompe con `AttributeError: 'dict'…strip()` → toda extracción scoped da 502 (falla cerrado, sin fuga) | **P1 (disponibilidad)** | `replicon/…/vault_client.py:92`, cadena desde `routes_skills.py:29-36` | Serializar el dict (`json.dumps`) antes de pasarlo al cliente (como sap_successfactors). Arregla también F3-1/F3-3 para replicon |

## P2 — endurecimiento (mismo bloque F3)

| ID | Título | Dónde | Arreglo |
|----|--------|-------|---------|
| F3-5 | Tabla scoped `sap_successfactors_tenant_entity_aliases` **sin política RLS** (+ bypass de scope vacío en `preflight.py:839-840`) | `infra/init/99zp_sap_successfactors_tenant_aliases.sql:9-13` | Enrolarla en RLS con el patrón `99w`/`99e` (ENABLE+FORCE+POLICY `omega_rls_workspace_matches`) |
| F3-6 | `request_admin_help` cae al `else` sin gate (no es fuga; riesgo de spam a admins) | `mcp-infra/app/main.py:1403-1404` | Clasificarlo (requerir contexto `trusted`/permiso) como los demás tools |
| F3-7 | Default `pg_user="postgres"` (superusuario, bypassea RLS) — latente; compose lo sobre-escribe | `mcp-infra/app/config.py:24` | Default no privilegiado o fail-fast si el rol es superuser/bypassrls |
| F3-8 | Llave Gemini vieja recuperable del historial (ya **revocada** por el owner) | `git show fdf1dd73:infra/.env.save` | Purgar del historial (`git filter-repo`) + gate de secret-scanning (gitleaks). Baja urgencia: la llave ya no sirve |

## Fuera de F3 (registrados para su fase — NO tocar ahora, respetar el orden)
- Profiler pierde estadísticas en el path staged → discovery muerto — **F8/F11/F12** (`publication_finalize.py` vs `main.py:2675`)
- `kb_talent_pipeline` CROSS JOIN fabrica datos — **F8/F11** (`knowledge_bits.yaml:94-95`)
- `gold_fetcher` LIMIT 5000 sin ORDER BY/flag de truncación — **F11/F12** (`gold_fetcher.py:128,194`)

## Regla de oro para todos
El cartucho de referencia correcto es **`sap_successfactors`** — no está afectado por ninguno de los 4 P1. Cada arreglo = copiar cómo lo hace él. Sin marcos nuevos, sin scripts nuevos. Prueba real (live-Postgres donde aplique), un cambio coherente a la vez, checkpoint por cambio. Sin prueba = BLOCKED, no DONE.
