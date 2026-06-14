# Plan de diseño — Unificación del runtime de cartuchos

> Objetivo: pasar de "1 contenedor FastAPI por cartucho" a **runtimes
> compartidos que ejecutan lo que el cartucho declara**. El cartucho
> queda como configuración + activos (DAGs, SQL, YAML), no como un
> servicio con su propio proceso.
>
> Estado: SOLO DISEÑO. No se toca código de runtime en este documento.

## 1. Hallazgo central (comparación de los dos repos)

Comparé `C:\MODecissionsPaaS` (predecesor, abr–may) contra
`C:\OMEGA-BETA\CONSOLA-BETA` (actual).

| Aspecto | MODecissionsPaaS (predecesor) | CONSOLA-BETA (actual) |
|---|---|---|
| Contenedor por cartucho | **No** — no hay servicios `replicon/sap_*` en compose | **Sí** — 6 servicios (replicon 8201, sap_hcm 8202, sap_sf 8203, sap_s4 8204, salesforce 8205, hubspot 8210) |
| Cartucho `app/` FastAPI + Dockerfile | Existe pero **vestigial** (no se despliega) | **Se construye y corre** ("Sprint v1.40 — restored from original") |
| Tools de cartucho | **Centralizadas** en `mcp-infra/app/tools/cartridges.py`, parametrizadas por `cartridge_id` | Duplicadas: per-cartridge `app/mcp_server.py` **y** `mcp-infra/app/tools/cartridges.py` (1390 líneas, más desarrollado que MOD) |
| Framework por cartucho | 1 cartucho, sin divergencia | Framework **duplicado y divergido** entre 6 cartuchos (`job_runner.py`, `main.py`, `mcp_server.py` difieren) |
| Registro de puertos | No aplica | `_CARTRIDGE_PORTS` hardcodeado en `console/app/routers/cartridges.py:36` |

**Conclusión:** MODecissionsPaaS ya tenía el modelo que pedimos
(runtime genérico central, cartucho = config). CONSOLA-BETA **regresó**
a contenedor-por-cartucho en v1.40 y ahora mantiene **ambos caminos en
paralelo** (redundante): los 6 contenedores duplican un framework que
además divergió, mientras `mcp-infra` ya tiene las tools genéricas.

Es decir: gran parte del runtime genérico **ya existe en CONSOLA-BETA**.
El trabajo no es construirlo de cero, sino **consolidar en él y borrar
los contenedores por cartucho**.

## 2. Modelo objetivo

Tres runtimes COMPARTIDOS ejecutan todo lo que trae cualquier cartucho,
resolviéndolo por `cartridge_id`. Ningún contenedor por cartucho.

| Responsabilidad | Runtime compartido | Qué lee del cartucho |
|---|---|---|
| Extracción (raw/bronze) | **Airflow** (ya monta `/registry/cartridges/<id>/dags`) | `dags/*.py`, `ap_flows/*.json`, `connector.yaml` |
| Tools MCP (preview, kb, semantic, manifest, extract-trigger) | **mcp-infra** (`tools/cartridges.py`, ya parametrizado por `cartridge_id`) | tablas Postgres (`entity_config`, `kb_config`, `mcp_custom_tools`) + S3 |
| Transformaciones silver/gold + queries | **refinement** (DuckDB, ya compartido) | `datasets/*.sql` |
| Manifiesto / catálogo / semántico | **Postgres** (`seed.sql` del cartucho) | `config/*.yaml`, `seed.sql` |
| Apps / hints | console (estático) | `apps/*.json|html`, `hints/*.md` |

El cartucho queda como: `config/` (yaml + seed.sql) + `datasets/*.sql`
+ `dags/*.py` + `apps/` + `hints/`. **Sin `app/` ni Dockerfile.**

Esto es exactamente "contenedores que se configuran para correr lo que
venga en el cartucho": los runtimes compartidos se parametrizan por
`cartridge_id` y ejecutan los activos declarados.

## 3. Lo que hay que cambiar (puntos de acoplamiento)

1. **compose** `infra/docker-compose.yml`: borrar los 6 servicios de
   cartucho (`replicon`, `hubspot`, `salesforce`, `sap-hcm`,
   `sap-s4hana`, `sap-successfactors`), sus puertos, healthchecks,
   `depends_on`, y las env `*_URL` / `INTERNAL_API_KEY_<CART>_TO_*`.
2. **Registro de puertos** `console/app/routers/cartridges.py:36`
   (`_CARTRIDGE_PORTS`) y `_cartridge_url()`: eliminar. Las rutas que
   hoy proxean `http://<cartridge>:<port>/mcp/*` deben reapuntar a
   `mcp-infra` con `cartridge_id` como parámetro.
3. **mcp_registry** `console/app/services/mcp_registry.py`: quitar los
   "builtin servers" por cartucho y `ALLOWED_MCP_HOSTS` de cartuchos;
   dejar solo `mcp-infra` (y `refinement`).
4. **Airflow**: confirmar/generalizar el montaje de DAGs para que tome
   `/registry/cartridges/*/dags` por glob, no entradas hardcodeadas por
   cartucho.
5. **Cartuchos**: retirar `app/` (FastAPI), `Dockerfile`,
   `requirements.txt` de cada cartucho. Mover cualquier lógica de
   extracción que solo viva en el `app/main.py`/`job_runner.py` del
   cartucho a (a) el DAG correspondiente o (b) una tool genérica en
   mcp-infra. **Auditar la divergencia** (`job_runner.py` de replicon
   hardcodea `'replicon'`; hubspot ya parametriza `CARTRIDGE_ID`) para
   no perder comportamiento al consolidar.
6. **Paridad de tools**: `mcp-infra/tools/cartridges.py` (1390 líneas)
   debe cubrir todo lo que hoy exponen los `app/mcp_server.py` por
   cartucho (list_entities, preview, extract, query_kb, run_kb,
   get_semantic, get_manifest, get_run_logs, custom tools). Hacer un
   diff de superficie de tools antes de borrar contenedores.

## 4. Riesgos y decisiones abiertas

- **SAP / aislamiento**: los cartuchos SAP corren bajo `profile: sap` y
  traen auth OData/OAuth particular. Hay que confirmar que la extracción
  SAP funciona como DAG + tool genérica sin el contenedor dedicado. Es
  el caso más delicado; migrar al final.
- **Por qué se "restauró" el contenedor en v1.40**: investigar el commit
  para no repetir el motivo (¿aislamiento de fallos? ¿límite de recursos?
  ¿credenciales por-cartucho?). Si fue por aislamiento, contemplar el
  modelo híbrido (un runtime genérico que puede aislar un cartucho
  pesado por config del tenant) en vez de borrado total.
- **Extracción pesada**: un cartucho con extracción intensiva ya no tiene
  su propio contenedor para escalar/limitar. Airflow worker pools o
  límites por DAG cubren esto, pero hay que dimensionarlo.
- **Credenciales por-cartucho**: hoy cada contenedor tiene su rol de
  Postgres least-privilege y sus `INTERNAL_API_KEY_<CART>_*`. Al
  consolidar, el runtime compartido necesita un modelo de scoping por
  `cartridge_id`/tenant (Vault ya guarda credenciales por cartucho).

## 5. Secuencia recomendada (cuando se implemente)

Migración incremental, un cartucho probado en vivo a la vez:

1. **Auditar paridad**: tabla tool-por-tool (per-cartridge `mcp_server`
   vs `mcp-infra/tools/cartridges`) y extracción (cartridge `app` vs
   DAG). Cerrar huecos en mcp-infra/DAGs **sin** borrar nada todavía.
2. **Piloto replicon**: apagar el contenedor `replicon`, enrutar sus
   rutas a mcp-infra, correr extracción por DAG. Validar
   smoke/e2e/acceptance del flujo replicon end-to-end.
3. **Generalizar console**: reemplazar `_CARTRIDGE_PORTS`/`_cartridge_url`
   por resolución vía mcp-infra; limpiar `mcp_registry`.
4. **HubSpot y Salesforce**: repetir (no-SAP primero).
5. **SAP (los 3)**: migrar al final, con foco en auth y aislamiento.
6. **Borrado final**: quitar `app/`+Dockerfile de cada cartucho, los 6
   servicios del compose, env y keys muertas. Quedan cartuchos
   puramente declarativos.

Cada paso es reversible (el contenedor se puede re-levantar) hasta el
paso 6.

## 6. Resultado

- Imagen/servicio por cartucho: de **6 → 0**.
- Framework duplicado/divergido: **eliminado** (vive una vez en los
  runtimes compartidos).
- Alta de cartucho nuevo: dejar de requerir Dockerfile + servicio compose
  + entrada en `_CARTRIDGE_PORTS`; pasa a ser soltar una carpeta
  declarativa en `/registry/cartridges` + `seed.sql`.
- Coincide con el modelo que MODecissionsPaaS ya probó, pero sobre la
  base más completa de CONSOLA-BETA (mcp-infra de 1390 líneas, RLS,
  entitlements por tenant).
